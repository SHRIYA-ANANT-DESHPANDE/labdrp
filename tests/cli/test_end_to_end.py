"""Full local HTTP workflow from student CLI through receiver storage."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from typer.testing import CliRunner

from labdrop.cli.app import app as student_app
from labdrop.server.admin import app as admin_app
from labdrop.server.main import app as receiver_app


def test_student_submit_and_verify_over_local_http(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "receiver.sqlite3"
    storage_path = tmp_path / "archives"
    monkeypatch.setenv("LABDROP_DB_PATH", str(db_path))
    monkeypatch.setenv("LABDROP_STORAGE_DIR", str(storage_path))
    admin = CliRunner()
    assert admin.invoke(admin_app, ["register-student", "--roll", "23", "--course", "OS"]).exit_code == 0
    assert admin.invoke(
        admin_app, ["create-assignment", "--assignment", "OS-Lab-05", "--course", "OS"]
    ).exit_code == 0
    assert admin.invoke(
        admin_app, ["assign-permission", "--roll", "23", "--assignment", "OS-Lab-05"]
    ).exit_code == 0
    token = admin.invoke(admin_app, ["create-token", "--roll", "23"]).output.splitlines()[-1].strip()

    assignment = tmp_path / "Lab05"
    assignment.mkdir()
    (assignment / "program.c").write_text('int main(void) { return 0; }\n', encoding="utf-8")
    (assignment / "terminal_log.txt").write_text("compile and run\n", encoding="utf-8")

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(receiver_app, host="127.0.0.1", port=port, log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "local FastAPI receiver did not start"
    monkeypatch.setenv("LABDROP_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr("labdrop.cli.app.getpass", lambda _prompt: token)
    try:
        result = CliRunner().invoke(
            student_app,
            ["submit", str(assignment), "--assignment", "OS-Lab-05"],
            input="y\n",
        )
        assert result.exit_code == 0, result.output
        assert "Receipt: LD-" in result.output

        with httpx.Client() as client:
            headers = {"Authorization": f"Bearer {token}"}
            history = client.get(f"http://127.0.0.1:{port}/submissions", headers=headers)
            assert history.status_code == 200
            receipt = history.json()[0]["receipt_id"]
            verification = client.get(f"http://127.0.0.1:{port}/submissions/{receipt}", headers=headers)
            assert verification.status_code == 200
            assert verification.json()["integrity"] == "verified"
        assert len(list(storage_path.glob("*.zip"))) == 1
    finally:
        server.should_exit = True
        thread.join(timeout=5)

