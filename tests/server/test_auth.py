"""Authentication and permission integration tests."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from labdrop.server.admin import app as admin_app
from labdrop.server.database import connect
from labdrop.server.main import app


def provision(tmp_path: Path, monkeypatch) -> tuple[str, Path]:
    db_path = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("LABDROP_DB_PATH", str(db_path))
    runner = CliRunner()
    assert runner.invoke(admin_app, ["register-student", "--roll", "23", "--course", "OS"]).exit_code == 0
    result = runner.invoke(admin_app, ["create-token", "--roll", "23"])
    assert result.exit_code == 0, result.output
    token = result.output.splitlines()[-1].strip()
    assert runner.invoke(
        admin_app, ["create-assignment", "--assignment", "OS-Lab-05", "--course", "OS"]
    ).exit_code == 0
    assert runner.invoke(
        admin_app, ["assign-permission", "--roll", "23", "--assignment", "OS-Lab-05"]
    ).exit_code == 0
    return token, db_path


def test_token_is_random_hash_only_and_maps_to_student(tmp_path: Path, monkeypatch) -> None:
    token, db_path = provision(tmp_path, monkeypatch)
    second = CliRunner().invoke(admin_app, ["create-token", "--roll", "23"])
    assert second.exit_code == 0
    another = second.output.splitlines()[-1].strip()
    assert len(token) >= 40
    assert token != another
    with connect(db_path) as db:
        hashes = [row[0] for row in db.execute("SELECT token_hash FROM tokens")]
        assert hashlib.sha256(token.encode()).hexdigest() in hashes
        assert token not in hashes
        assert token.encode() not in db_path.read_bytes()


def test_authentication_and_assignment_permission(tmp_path: Path, monkeypatch) -> None:
    token, _ = provision(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/auth/me").status_code == 401
        assert client.get("/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get("/auth/me", headers=headers)
        assert response.json() == {"roll": "23", "course": "OS"}
        assert client.get("/assignments/OS-Lab-05/access", headers=headers).status_code == 200
        assert client.get("/assignments/OTHER/access", headers=headers).status_code == 403
        assert client.get("/auth/me", params={"roll": "999"}, headers=headers).json()["roll"] == "23"
        assert client.post("/admin/register-student", json={"roll": "intruder"}).status_code == 404


def test_revocation_is_immediate_and_expired_tokens_rejected(tmp_path: Path, monkeypatch) -> None:
    token, db_path = provision(tmp_path, monkeypatch)
    runner = CliRunner()
    expired_result = runner.invoke(
        admin_app,
        ["create-token", "--roll", "23", "--expires", (date.today() - timedelta(days=1)).isoformat()],
    )
    assert expired_result.exit_code == 0
    expired = expired_result.output.splitlines()[-1].strip()
    with TestClient(app) as client:
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert runner.invoke(admin_app, ["revoke-token", "--roll", "23"]).exit_code == 0
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    with connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM audit_events").fetchone()[0] >= 5


def test_provisioning_constraints_and_transaction_rollback(tmp_path: Path, monkeypatch) -> None:
    _, db_path = provision(tmp_path, monkeypatch)
    runner = CliRunner()
    duplicate = runner.invoke(admin_app, ["register-student", "--roll", "23", "--course", "OS"])
    assert duplicate.exit_code != 0
    with connect(db_path) as db:
        with pytest.raises(sqlite3.IntegrityError), db:
            db.execute(
                "INSERT INTO students(roll, course, created_at) VALUES (?, ?, ?)",
                ("rollback", "OS", "now"),
            )
            db.execute(
                "INSERT INTO tokens(student_id, token_hash, created_at) VALUES (?, ?, ?)",
                (99999, "a" * 64, "now"),
            )
        assert db.execute("SELECT count(*) FROM students WHERE roll = 'rollback'").fetchone()[0] == 0
    unauthorized = runner.invoke(admin_app, ["create-token", "--roll", "missing"])
    assert unauthorized.exit_code != 0
