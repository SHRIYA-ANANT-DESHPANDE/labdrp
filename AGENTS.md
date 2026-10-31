# AGENTS.md — LabDrop

LabDrop is a cross-platform CLI that records a student's terminal session and submits a lab assignment (source files + terminal log + manifest) to an instructor-run FastAPI receiver. Students never log into Google or touch the instructor's storage.

## Working rules
- Work in small stages. After each stage: run tests, explain what was built, give me exact manual test commands, then STOP and wait for my confirmation.
- Inspect existing code before changing it.
- No fake functionality. No placeholder implementations presented as complete. No tests that only assert a function exists. If something is incomplete (Windows recorder, Drive adapter), say so explicitly.
- Linux is the priority. Develop in WSL2 but never use WSL-specific features in app code. Keep it working on native Ubuntu.
- Windows support lives in a separate adapter (ConPTY). Do not claim it works until it has been tested on Windows.
- Python: `requires-python >=3.10`. Lint with `ruff check .`, test with `python -m pytest -v`.

## Architecture
- `labdrop/cli/` — commands, recorder (base/linux/windows), packager, auth, client
- `labdrop/server/` — FastAPI app, auth, submissions, storage adapter, drive_adapter (stub until a later milestone)
- Keep CLI, recorder, packager and receiver separated. The CLI and server must not import each other's internals; share only a small `common/` module for manifest/schema models if needed.

## Decisions already made
- Terminal log: `labdrop capture` writes `terminal_log.txt` to the current directory by default (`--output PATH` overrides). `labdrop submit` looks for the log in the assignment folder first, then the current directory, or uses `--log PATH`. The log is copied into the ZIP.
- Recorder: capture raw PTY bytes, then render them through a terminal emulator (`pyte`) so backspaces, carriage returns, tab completion and arrow keys produce a readable log. Do not regex-strip ANSI.
- Hidden input (passwords) must not appear in the log. Echo-off input is not echoed by the PTY, so verify this with a test rather than assuming.
- Packager excludes: `.git`, `.venv`, `venv`, `__pycache__`, `node_modules`, temp/editor files, compiled binaries and object files (`a.out`, `*.o`, `*.exe`, `*.so`, `*.class`, `*.pyc`). Show excluded files in the preview.
- Receipt IDs: human-readable prefix plus a random suffix (e.g. `LD-2026-K7Q4X9M2`), never sequential. `verify` requires the student token and only returns that student's own submissions.
- The server timestamp is authoritative; ignore any client timestamp.
- Tokens: generated with `secrets`, only SHA-256 (or stronger) hashes stored server-side, entered via `getpass`, never in argv or logs, never saved by default. Support revocation and an optional expiry date. Student identity comes from the token, never from CLI input.

## Server security requirements
- ZIP validation: reject path traversal, absolute paths, symlinks; cap file count, per-file size, total compressed AND uncompressed size (zip bombs).
- Validate the manifest and verify every SHA-256 against the actual file contents.
- Never execute submitted code.
- Immutable versioned submissions; never silently overwrite. Audit log. Rate limiting. Per-student course/assignment permissions.
- A student can never list, read, modify or delete another student's data.
- Storage goes through an adapter interface; MVP uses local disk. Drive credentials stay server-side.
- Local dev uses `http://127.0.0.1:8000`. Production must be HTTPS.

## Honesty
Terminal logs are locally generated and editable by students. Never describe them as tamper-proof proof of execution, in code, docs or README.

## Never commit
Real tokens, API keys, `.env`, credentials, real submissions, databases with real data.