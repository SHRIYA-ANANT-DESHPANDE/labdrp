"""Student upload client tests using a local mock HTTP client."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner
from typing_extensions import Self

from labdrop.cli.app import app
from labdrop.cli.client import submissions
from labdrop.cli.client.submissions import SubmissionError
from labdrop.cli.packager import plan_package


class FakeResponse:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self.body = body

    def json(self) -> dict:
        return self.body


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.headers: dict[str, str] = {}
        self.archive_names: list[str] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args) -> None:
        return None

    def post(self, _url: str, *, headers: dict[str, str], files: dict) -> FakeResponse:
        self.headers = headers
        stream = files["file"][1]
        with zipfile.ZipFile(io.BytesIO(stream.read())) as archive:
            self.archive_names = archive.namelist()
        return self.response


def test_client_uploads_zip_with_token_and_returns_receipt(tmp_path: Path, monkeypatch) -> None:
    assignment = tmp_path / "Lab"
    assignment.mkdir()
    (assignment / "program.c").write_text("source", encoding="utf-8")
    (assignment / "terminal_log.txt").write_text("log", encoding="utf-8")
    plan = plan_package(assignment, "OS-Lab-05", cwd=tmp_path)
    fake = FakeClient(FakeResponse(201, {"receipt_id": "LD-2026-ABCDEFG2", "submitted_at": "now"}))
    monkeypatch.setattr(submissions.httpx, "Client", lambda **_kwargs: fake)
    monkeypatch.setenv("LABDROP_URL", "http://localhost:8000/")

    result = submissions.submit_plan(plan, "secret-student-token")

    assert result["receipt_id"] == "LD-2026-ABCDEFG2"
    assert fake.headers["Authorization"] == "Bearer secret-student-token"
    assert set(fake.archive_names) == {"program.c", "terminal_log.txt", "manifest.json"}


@pytest.mark.parametrize("status, message", [(401, "authentication failed"), (403, "not authorized"), (429, "rate limit")])
def test_http_errors_are_safe_and_do_not_echo_token(tmp_path: Path, monkeypatch, status: int, message: str) -> None:
    assignment = tmp_path / "Lab"
    assignment.mkdir()
    (assignment / "terminal_log.txt").write_text("log", encoding="utf-8")
    plan = plan_package(assignment, "A", cwd=tmp_path)
    fake = FakeClient(FakeResponse(status, {"detail": "error"}))
    monkeypatch.setattr(submissions.httpx, "Client", lambda **_kwargs: fake)
    with pytest.raises(SubmissionError, match=message) as error:
        submissions.submit_plan(plan, "do-not-print-this-token")
    assert "do-not-print-this-token" not in str(error.value)


def test_submit_cli_prompts_without_echoing_token(tmp_path: Path, monkeypatch) -> None:
    assignment = tmp_path / "Lab"
    assignment.mkdir()
    (assignment / "terminal_log.txt").write_text("log", encoding="utf-8")
    monkeypatch.setattr("labdrop.cli.app.getpass", lambda _prompt: "private-token")
    monkeypatch.setattr(
        "labdrop.cli.app.submit_plan",
        lambda _plan, token: {"receipt_id": "LD-2026-ABCDEFG2", "submitted_at": "server-time"},
    )

    result = CliRunner().invoke(app, ["submit", str(assignment), "--assignment", "A"], input="y\n")

    assert result.exit_code == 0, result.output
    assert "LD-2026-ABCDEFG2" in result.output
    assert "private-token" not in result.output


def test_submit_defaults_to_cancel_and_does_not_prompt_for_token(tmp_path: Path, monkeypatch) -> None:
    assignment = tmp_path / "Lab"
    assignment.mkdir()
    (assignment / "terminal_log.txt").write_text("log", encoding="utf-8")
    monkeypatch.setattr("labdrop.cli.app.getpass", lambda _prompt: pytest.fail("unexpected token prompt"))
    monkeypatch.setattr("labdrop.cli.app.submit_plan", lambda *_args: pytest.fail("unexpected upload"))

    result = CliRunner().invoke(app, ["submit", str(assignment), "--assignment", "A"], input="\n")

    assert result.exit_code == 0, result.output
    assert "Submission cancelled" in result.output
