"""Interactive Linux PTY recorder with pyte terminal emulation."""

from __future__ import annotations

import codecs
import errno
import fcntl
import os
import pty
import select
import shlex
import signal
import struct
import termios
import time
from typing import NoReturn

import pyte


class RecorderError(RuntimeError):
    """Raised when an interactive terminal session cannot be recorded."""


class LinuxRecorder:
    """Run a shell in a PTY, forward terminal traffic, and render its final screen."""

    def __init__(self) -> None:
        self._master_fd: int | None = None
        self._child_pid: int | None = None
        self._old_termios: list[object] | None = None
        self._old_winch_handler: object | None = None
        self._resize_pending = False
        self._screen: pyte.HistoryScreen | None = None
        self._stream: pyte.Stream | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def run(self) -> str:
        """Run the configured interactive shell and return the rendered transcript."""
        if os.name != "posix" or not os.isatty(0) or not os.isatty(1):
            raise RecorderError("capture requires an interactive Linux terminal")

        columns, rows = self._terminal_size()
        self._screen = pyte.HistoryScreen(columns, rows, history=10000)
        self._stream = pyte.Stream(self._screen)

        pid, master_fd = pty.fork()
        if pid == 0:
            self._exec_shell()
        self._child_pid = pid
        self._master_fd = master_fd

        try:
            self._old_termios = termios.tcgetattr(0)
            tty_attrs = termios.tcgetattr(0)
            tty_attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
            tty_attrs[6][termios.VMIN] = 1
            tty_attrs[6][termios.VTIME] = 0
            termios.tcsetattr(0, termios.TCSADRAIN, tty_attrs)
            self._old_winch_handler = signal.signal(signal.SIGWINCH, self._on_winch)
            self._set_pty_size()
            self._relay()
            return self._render()
        finally:
            try:
                self._restore_terminal()
            finally:
                self._clean_up_child()

    @staticmethod
    def _exec_shell() -> NoReturn:
        shell = os.environ.get("SHELL") or "/bin/sh"
        shell_args = shlex.split(os.environ.get("LABDROP_SHELL_ARGS", ""))
        shell_name = os.path.basename(shell)
        argv = [shell, f"-{shell_name}", *shell_args]
        try:
            os.execvpe(shell, argv, os.environ.copy())
        except OSError as exc:
            os.write(2, f"labdrop: cannot start shell {shell}: {exc}\r\n".encode())
            os._exit(127)

    @staticmethod
    def _terminal_size() -> tuple[int, int]:
        packed = fcntl.ioctl(1, termios.TIOCGWINSZ, b"\0" * 8)
        rows, columns, _, _ = struct.unpack("HHHH", packed)
        return max(columns, 1), max(rows, 1)

    def _set_pty_size(self) -> None:
        assert self._master_fd is not None
        columns, rows = self._terminal_size()
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        if self._screen is not None:
            self._screen.resize(lines=rows, columns=columns)

    def _on_winch(self, _signum: int, _frame: object) -> None:
        self._resize_pending = True

    def _relay(self) -> None:
        assert self._master_fd is not None
        input_open = True
        while True:
            if self._resize_pending:
                self._resize_pending = False
                self._set_pty_size()

            try:
                readers = [self._master_fd]
                if input_open:
                    readers.append(0)
                ready, _, _ = select.select(readers, [], [], 0.1)
            except InterruptedError:
                continue

            if 0 in ready:
                try:
                    user_input = os.read(0, 4096)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        user_input = b""
                    else:
                        raise
                if user_input:
                    self._write_pty(user_input)
                else:
                    input_open = False

            if self._master_fd in ready:
                try:
                    output = os.read(self._master_fd, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not output:
                    break
                self._record_output(output)

            assert self._child_pid is not None
            ended, _status = os.waitpid(self._child_pid, os.WNOHANG)
            if ended:
                self._child_pid = None
                while True:
                    ready, _, _ = select.select([self._master_fd], [], [], 0)
                    if not ready:
                        break
                    try:
                        output = os.read(self._master_fd, 65536)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not output:
                        break
                    self._record_output(output)
                break

    def _record_output(self, output: bytes) -> None:
        assert self._stream is not None
        self._stream.feed(self._decoder.decode(output))
        self._write_stdout(output)

    def _render(self) -> str:
        assert self._screen is not None
        assert self._stream is not None
        final_text = self._decoder.decode(b"", final=True)
        if final_text:
            self._stream.feed(final_text)
        lines = ["".join(line).rstrip() for line in self._screen.history.top]
        lines.extend(line.rstrip() for line in self._screen.display)
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines) + ("\n" if lines else "")

    def _restore_terminal(self) -> None:
        if self._old_termios is not None:
            termios.tcsetattr(0, termios.TCSADRAIN, self._old_termios)
            self._old_termios = None
        if self._old_winch_handler is not None:
            signal.signal(signal.SIGWINCH, self._old_winch_handler)
            self._old_winch_handler = None

    def _clean_up_child(self) -> None:
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

        if self._child_pid is None:
            return

        pid = self._child_pid
        self._child_pid = None
        for sig in (signal.SIGHUP, signal.SIGTERM):
            try:
                os.killpg(pid, sig)
            except ProcessLookupError:
                pass
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                try:
                    ended, _ = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    return
                if ended:
                    return
                time.sleep(0.02)

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass

    def _write_pty(self, data: bytes) -> None:
        assert self._master_fd is not None
        view = memoryview(data)
        while view:
            view = view[os.write(self._master_fd, view) :]

    @staticmethod
    def _write_stdout(data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(1, view) :]
