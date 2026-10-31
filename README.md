# LabDrop

LabDrop records an interactive Linux terminal session, packages assignment files with a terminal log and SHA-256 manifest, and sends the archive to an instructor-controlled receiver. Terminal logs are editable local records, not proof of execution.

## Implemented

- `labdrop capture [--output PATH]` — records a genuine Linux PTY shell. The default log is `terminal_log.txt` in the current directory.
- `labdrop package DIRECTORY --assignment ID [--log PATH] [--output PATH]` — previews included/excluded files and creates a ZIP with `manifest.json`.
- `labdrop submit DIRECTORY --assignment ID [--log PATH]` — packages and uploads; prompts for the token without echoing or saving it.
- `labdrop configure`, `labdrop status`, `labdrop history`, and `labdrop verify RECEIPT_ID`.
- `labdrop-admin` local commands for student/token provisioning and assignment permissions.
- FastAPI receiver routes for health, authentication, assignment access, submission upload, history, and receipt verification.

Windows ConPTY, instructor web UI, Drive storage, and receipt download are not implemented.

## Install

Python 3.10 or later is required. On Ubuntu/WSL:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Student workflow

Record a session, then exit the captured shell to write the log:

```bash
labdrop capture
```

Prepare or submit an assignment:

```bash
labdrop package ./Lab05 --assignment OS-Lab-05
labdrop submit ./Lab05 --assignment OS-Lab-05
```

Use `--log PATH` to override log discovery. Otherwise LabDrop checks the assignment directory, then the current directory. The receiver URL defaults to `http://127.0.0.1:8000`; `labdrop configure` saves a non-secret URL and `LABDROP_URL` overrides it. Non-local production URLs must use HTTPS.

Tokens are requested with `getpass`, never saved by default, and are not included in logs. Student identity is determined by the token.

## Instructor setup

Run the receiver on loopback for local development:

```bash
export LABDROP_DB_PATH="$HOME/.local/share/labdrop/admin.sqlite3"
export LABDROP_STORAGE_DIR="$HOME/.local/share/labdrop/submissions"
uvicorn labdrop.server.main:app --host 127.0.0.1 --port 8000
```

Provision records locally; there are no public provisioning endpoints:

```bash
labdrop-admin register-student --roll 23 --course OS
labdrop-admin create-assignment --assignment OS-Lab-05 --course OS
labdrop-admin assign-permission --roll 23 --assignment OS-Lab-05
labdrop-admin create-token --roll 23
```

The generated token is displayed once. The database stores only SHA-256 hashes. Revoke active tokens with `labdrop-admin revoke-token --roll 23`. Optional token expiry uses `--expires YYYY-MM-DD`.

## Receiver API

- `GET /health`
- `GET /auth/me`
- `GET /assignments/{assignment_id}/access`
- `POST /submissions/{assignment_id}` (multipart field `file`)
- `GET /submissions` (authenticated student's history)
- `GET /submissions/{receipt_id}` (owner-only integrity verification)

The receiver validates ZIP paths, symlinks, duplicate entries, resource limits, manifest contents, and SHA-256 checksums. Submitted code is never executed. Receipt IDs and timestamps are server-generated. Local disk is the current storage backend.

Use HTTPS outside loopback development. The receiver does not yet provide deployment automation, backups, or a public provisioning interface.

## Tests and lint

```bash
ruff check .
python -m pytest -v
```

CI covers Ubuntu 22.04 and 24.04 with Python 3.10, 3.11, and 3.12.
