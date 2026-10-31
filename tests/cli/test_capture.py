"""End-to-end tests for the interactive capture command."""

from __future__ import annotations

import fcntl
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux PTY integration tests")


class CaptureSession:
    """Drive labdrop capture through a real outer PTY."""

    prompt = b"$ "

    def __init__(self, directory: Path, output: Path | None = None) -> None:
        self.directory = directory
        self.log_path = output or directory / "terminal_log.txt"
        self.pid = -1
        self.master = -1
        self.buffer = bytearray()
        self.captured = bytearray()
        self.terminal_attrs_after: list[object] | None = None

    def start(self) -> None:
        pid, master = pty.fork()
        if pid == 0:
            os.chdir(self.directory)
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(self.directory),
                    "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                    "SHELL": "/bin/bash",
                    "TERM": "xterm-256color",
                    "LANG": "C.UTF-8",
                }
            )
            args = [sys.executable, "-m", "labdrop.cli.app", "capture"]
            if self.log_path != self.directory / "terminal_log.txt":
                args.extend(["--output", str(self.log_path)])
            os.execvpe(args[0], args, env)
        self.pid, self.master = pid, master
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        self.read_until(self.prompt)

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def command(self, command: str) -> bytes:
        self.send(command.encode() + b"\n")
        return self.read_until(self.prompt)

    def read_until(self, expected: bytes, timeout: float = 8) -> bytes:
        deadline = time.monotonic() + timeout
        while expected not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"Timed out waiting for {expected!r}; received {bytes(self.buffer)!r}")
            ready, _, _ = select.select([self.master], [], [], remaining)
            if not ready:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.buffer.extend(chunk)
            self.captured.extend(chunk)
        result = bytes(self.buffer)
        self.buffer.clear()
        return result

    def finish(self, command: bytes = b"exit\n") -> None:
        if command:
            self.send(command)
        _, status = os.waitpid(self.pid, 0)
        self.pid = -1
        output = self.read_remaining()
        assert os.waitstatus_to_exitcode(status) == 0, bytes(self.captured + output).decode(errors="replace")

    def read_remaining(self) -> bytes:
        result = bytearray(self.buffer)
        self.buffer.clear()
        while True:
            ready, _, _ = select.select([self.master], [], [], 0.2)
            if not ready:
                break
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            result.extend(chunk)
        self.terminal_attrs_after = termios.tcgetattr(self.master)
        os.close(self.master)
        self.master = -1
        return bytes(result)

    def close(self) -> None:
        if self.pid > 0:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass
        if self.master >= 0:
            os.close(self.master)


def test_capture_records_commands_output_and_default_path(tmp_path: Path) -> None:
    session = CaptureSession(tmp_path)
    try:
        session.start()
        session.command("printf 'FIRST_OUTPUT\\n'")
        session.command("printf 'SECOND_OUTPUT\\n'")
        session.finish()

        transcript = session.log_path.read_text(encoding="utf-8")
        assert transcript.index("FIRST_OUTPUT") < transcript.index("SECOND_OUTPUT")
        assert "printf 'FIRST_OUTPUT" in transcript
        assert "printf 'SECOND_OUTPUT" in transcript
    finally:
        session.close()


def test_capture_supports_interactive_input_and_hidden_input(tmp_path: Path) -> None:
    session = CaptureSession(tmp_path)
    try:
        session.start()
        session.send(b"read -r answer; printf 'ANSWER=%s\\n' \"$answer\"\n")
        session.read_until(b"read -r answer")
        session.send(b"visible_value\n")
        session.read_until(session.prompt)

        session.command("stty -echo")
        session.send(b"read -r secret; stty echo; printf 'SECRET_READ\\n'\n")
        session.send(b"hidden_value\n")
        session.read_until(session.prompt)
        session.finish()

        transcript = session.log_path.read_text(encoding="utf-8")
        assert "visible_value" in transcript
        assert "ANSWER=visible_value" in transcript
        assert "SECRET_READ" in transcript
        assert "hidden_value" not in transcript
    finally:
        session.close()


def test_capture_renders_line_editing_carriage_return_and_ansi(tmp_path: Path) -> None:
    session = CaptureSession(tmp_path)
    try:
        session.start()
        session.send(b"printf 'BAD_TOKEN'")
        session.send(b"\x01\x0b")  # readline: beginning of line, then kill to end
        session.send(b"printf 'GOOD_TOKEN\\n'\n")
        session.read_until(session.prompt)
        session.command("printf '\\101\\r\\102\\n'")
        session.command("printf '\\033[31mANSI_TOKEN\\033[0m\\n'")
        session.finish()

        transcript = session.log_path.read_text(encoding="utf-8")
        assert "GOOD_TOKEN" in transcript
        assert "BAD_TOKEN" not in transcript
        assert "B" in transcript
        assert "ANSI_TOKEN" in transcript
        assert "\x1b" not in transcript
    finally:
        session.close()


def test_capture_supports_arrow_history_and_tab_completion(tmp_path: Path) -> None:
    (tmp_path / "completion_target.txt").write_text("TAB_COMPLETION_OUTPUT\n", encoding="utf-8")
    session = CaptureSession(tmp_path)
    try:
        session.start()
        session.command("printf 'ARROW_HISTORY_OUTPUT\\n'")
        session.send(b"\x1b[A\n")
        session.read_until(session.prompt)
        session.send(b"cat completion_tar\t\n")
        session.read_until(session.prompt)
        session.finish()

        transcript = session.log_path.read_text(encoding="utf-8")
        assert transcript.count("ARROW_HISTORY_OUTPUT") >= 2
        assert "completion_target.txt" in transcript
        assert "TAB_COMPLETION_OUTPUT" in transcript
    finally:
        session.close()


@pytest.mark.skipif(shutil.which("gcc") is None, reason="GCC is required for this integration test")
def test_capture_records_interactive_gcc_program_runs(tmp_path: Path) -> None:
    source = tmp_path / "program.c"
    source.write_text(
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "  int a, b;\n"
        "  printf(\"Enter two numbers: \");\n"
        "  fflush(stdout);\n"
        "  if (scanf(\"%d %d\", &a, &b) != 2) return 1;\n"
        "  printf(\"Sum = %d\\n\", a + b);\n"
        "  return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(["gcc", "program.c", "-o", "a.out"], cwd=tmp_path, check=True)

    session = CaptureSession(tmp_path)
    try:
        session.start()
        session.command("gcc program.c -o a.out")
        session.send(b"./a.out\n")
        session.read_until(b"Enter two numbers: ")
        session.send(b"5 10\n")
        session.read_until(session.prompt)
        session.send(b"./a.out\n")
        session.read_until(b"Enter two numbers: ")
        session.send(b"20 30\n")
        session.read_until(session.prompt)
        session.finish()

        transcript = session.log_path.read_text(encoding="utf-8")
        assert transcript.index("gcc program.c -o a.out") < transcript.index("Sum = 15")
        assert "5 10" in transcript
        assert "Sum = 15" in transcript
        assert "20 30" in transcript
        assert "Sum = 50" in transcript
    finally:
        session.close()


def test_capture_handles_ctrl_c_and_ctrl_d(tmp_path: Path) -> None:
    session = CaptureSession(tmp_path)
    try:
        session.start()
        session.send(b"sleep 30\n")
        session.send(b"\x03")
        session.read_until(session.prompt)
        session.finish(command=b"\x04")
        assert session.log_path.exists()
        assert session.terminal_attrs_after is not None
        assert session.terminal_attrs_after[3] & termios.ECHO
        assert session.terminal_attrs_after[3] & termios.ICANON
    finally:
        session.close()


def test_capture_writes_custom_output_path(tmp_path: Path) -> None:
    custom_path = tmp_path / "logs" / "lab-session.txt"
    custom_path.parent.mkdir()
    session = CaptureSession(tmp_path, custom_path)
    try:
        session.start()
        session.command("printf 'CUSTOM_PATH_OUTPUT\\n'")
        session.finish()

        assert custom_path.is_file()
        assert "CUSTOM_PATH_OUTPUT" in custom_path.read_text(encoding="utf-8")
        assert not (tmp_path / "terminal_log.txt").exists()
    finally:
        session.close()
