"""Tests for receiver configuration and student history/verification commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from labdrop.cli.app import app
from labdrop.cli.client import submissions
from labdrop.cli.client.submissions import SubmissionError


def test_receiver_config_is_non_secret_and_environment_overrides(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = submissions.save_receiver_url("https://receiver.example.edu/")
    assert json.loads(path.read_text()) == {"receiver_url": "https://receiver.example.edu"}
    assert submissions.receiver_url() == "https://receiver.example.edu"
    monkeypatch.setenv("LABDROP_URL", "http://127.0.0.1:8000/")
    assert submissions.receiver_url() == "http://127.0.0.1:8000"


def test_receiver_rejects_plain_http_outside_localhost() -> None:
    with pytest.raises(SubmissionError, match="HTTPS"):
        submissions.validate_receiver_url("http://receiver.example.edu")


def test_unsafe_environment_url_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("LABDROP_URL", "http://receiver.example.edu")
    with pytest.raises(SubmissionError, match="HTTPS"):
        submissions.receiver_url()


def test_history_and_verify_prompt_token_without_displaying_it(monkeypatch) -> None:
    monkeypatch.setattr("labdrop.cli.app.getpass", lambda _prompt: "secret-token")
    seen: list[str] = []

    def fake_request(path: str, token: str):
        seen.append(token)
        if path == "/submissions":
            return [{"receipt_id": "LD-2026-ABCDEFG2", "assignment_id": "A", "submitted_at": "now"}]
        return {"receipt_id": "LD-2026-ABCDEFG2", "assignment_id": "A", "submitted_at": "now", "integrity": "verified"}

    monkeypatch.setattr("labdrop.cli.app.request_student", fake_request)
    runner = CliRunner()
    history = runner.invoke(app, ["history"])
    verified = runner.invoke(app, ["verify", "LD-2026-ABCDEFG2"])
    assert history.exit_code == verified.exit_code == 0
    assert "LD-2026-ABCDEFG2" in history.output and "verified" in verified.output
    assert "secret-token" not in history.output + verified.output
    assert seen == ["secret-token", "secret-token"]


def test_version_option(capsys) -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
