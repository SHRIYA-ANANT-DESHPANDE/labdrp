"""Secure receiver intake and receipt ownership tests."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from labdrop.server.main import app
from tests.server.test_auth import provision


def make_zip(assignment: str = "OS-Lab-05", *, path: str = "program.c", data: bytes = b"source") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(path, data)
        archive.writestr(
            "manifest.json",
            json.dumps({
                "format": "labdrop-submission", "format_version": 1, "assignment_id": assignment,
                "files": [{"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
            }),
        )
    return buffer.getvalue()


def test_valid_submission_stored_and_receipt_owned(tmp_path: Path, monkeypatch) -> None:
    token, _ = provision(tmp_path, monkeypatch)
    monkeypatch.setenv("LABDROP_STORAGE_DIR", str(tmp_path / "stored"))
    with TestClient(app) as client:
        response = client.post(
            "/submissions/OS-Lab-05", headers={"Authorization": f"Bearer {token}"},
            files={"file": ("submission.zip", make_zip(), "application/zip")},
        )
        assert response.status_code == 201, response.text
        receipt = response.json()["receipt_id"]
        assert receipt.startswith("LD-")
        verification = client.get(f"/submissions/{receipt}", headers={"Authorization": f"Bearer {token}"})
        assert verification.status_code == 200
        assert verification.json()["assignment_id"] == "OS-Lab-05"
        assert verification.json()["integrity"] == "verified"
        assert len(client.get("/submissions", headers={"Authorization": f"Bearer {token}"}).json()) == 1
        assert list((tmp_path / "stored").glob("*.zip"))
        stored = next((tmp_path / "stored").glob("*.zip"))
        stored.write_bytes(b"modified")
        assert client.get(f"/submissions/{receipt}", headers={"Authorization": f"Bearer {token}"}).status_code == 409


def test_rejects_bad_manifest_and_unauthorized_assignment(tmp_path: Path, monkeypatch) -> None:
    token, _ = provision(tmp_path, monkeypatch)
    with TestClient(app) as client:
        bad = client.post(
            "/submissions/OS-Lab-05", headers={"Authorization": f"Bearer {token}"},
            files={"file": ("bad.zip", make_zip(path="../escape"), "application/zip")},
        )
        assert bad.status_code == 400
        denied = client.post(
            "/submissions/OTHER", headers={"Authorization": f"Bearer {token}"},
            files={"file": ("ok.zip", make_zip("OTHER"), "application/zip")},
        )
        assert denied.status_code == 403


def test_rejects_checksum_mismatch_and_receipt_is_private(tmp_path: Path, monkeypatch) -> None:
    token, _ = provision(tmp_path, monkeypatch)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("program.c", b"tampered")
        zf.writestr("manifest.json", json.dumps({
            "format": "labdrop-submission", "format_version": 1, "assignment_id": "OS-Lab-05",
            "files": [{"path": "program.c", "size": 6, "sha256": hashlib.sha256(b"source").hexdigest()}],
        }))
    with TestClient(app) as client:
        response = client.post(
            "/submissions/OS-Lab-05", headers={"Authorization": f"Bearer {token}"},
            files={"file": ("bad.zip", archive.getvalue(), "application/zip")},
        )
        assert response.status_code == 400
        assert client.get("/submissions/LD-2026-NOTMINE", headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_history_and_receipt_are_isolated_between_students(tmp_path: Path, monkeypatch) -> None:
    token, _ = provision(tmp_path, monkeypatch)
    from typer.testing import CliRunner

    from labdrop.server.admin import app as admin_app

    runner = CliRunner()
    assert runner.invoke(admin_app, ["register-student", "--roll", "99", "--course", "OS"]).exit_code == 0
    second_token = runner.invoke(admin_app, ["create-token", "--roll", "99"]).output.splitlines()[-1].strip()
    with TestClient(app) as client:
        accepted = client.post("/submissions/OS-Lab-05", headers={"Authorization": f"Bearer {token}"},
                               files={"file": ("submission.zip", make_zip(), "application/zip")})
        receipt = accepted.json()["receipt_id"]
        headers = {"Authorization": f"Bearer {second_token}"}
        assert client.get("/submissions", headers=headers).json() == []
        assert client.get(f"/submissions/{receipt}", headers=headers).status_code == 404


def test_duplicate_zip_paths_and_resource_limits_are_rejected(tmp_path: Path, monkeypatch) -> None:
    import labdrop.server.submissions as receiver

    token, _ = provision(tmp_path, monkeypatch)
    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("x.txt", b"one")
        archive.writestr("x.txt", b"two")
        archive.writestr("manifest.json", "{}")
    monkeypatch.setattr(receiver, "MAX_FILE", 10)
    with TestClient(app) as client:
        response = client.post("/submissions/OS-Lab-05", headers={"Authorization": f"Bearer {token}"},
                               files={"file": ("duplicate.zip", duplicate.getvalue(), "application/zip")})
        assert response.status_code == 400
        assert "duplicate" in response.json()["detail"]
        monkeypatch.setattr(receiver, "MAX_FILE", 2)
        oversized = make_zip(data=b"more than two bytes")
        response = client.post("/submissions/OS-Lab-05", headers={"Authorization": f"Bearer {token}"},
                               files={"file": ("large.zip", oversized, "application/zip")})
        assert response.status_code == 400
        assert "size limit" in response.json()["detail"]
