"""Submit a prepared assignment archive without persisting credentials."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from labdrop.cli.packager import PackagePlan, write_package


class SubmissionError(RuntimeError):
    """A safe, user-displayable submission failure."""


def receiver_url() -> str:
    if env_url := os.environ.get("LABDROP_URL"):
        return validate_receiver_url(env_url)
    try:
        value = json.loads(config_path().read_text(encoding="utf-8"))["receiver_url"]
        if isinstance(value, str):
            return validate_receiver_url(value)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return "http://127.0.0.1:8000"


def config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "labdrop" / "config.json"


def validate_receiver_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise SubmissionError("receiver URL must be an http(s) URL without embedded credentials")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise SubmissionError("non-local receiver URLs must use HTTPS")
    return value.strip().rstrip("/")


def save_receiver_url(value: str) -> Path:
    url = validate_receiver_url(value)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps({"receiver_url": url}, indent=2) + "\n", encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def request_student(path: str, token: str) -> object:
    if not token:
        raise SubmissionError("a student token is required")
    try:
        response = httpx.get(
            f"{receiver_url()}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=15
        )
    except httpx.HTTPError as exc:
        raise SubmissionError("could not contact the submission receiver") from exc
    if response.status_code == 401:
        raise SubmissionError("authentication failed; check the student token")
    if response.status_code >= 400:
        raise SubmissionError(f"receiver rejected the request (HTTP {response.status_code})")
    try:
        return response.json()
    except ValueError as exc:
        raise SubmissionError("receiver returned an invalid response") from exc


def submit_plan(plan: PackagePlan, token: str) -> dict[str, str]:
    if not token:
        raise SubmissionError("a student token is required")
    with tempfile.TemporaryDirectory(prefix="labdrop-submit-") as folder:
        archive_path = Path(folder) / "submission.zip"
        write_package(plan, archive_path)
        try:
            with httpx.Client(timeout=30) as client, archive_path.open("rb") as archive:
                response = client.post(
                    f"{receiver_url()}/submissions/{plan.assignment_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    files={"file": ("submission.zip", archive, "application/zip")},
                )
        except httpx.HTTPError as exc:
            raise SubmissionError("could not contact the submission receiver") from exc
        if response.status_code == 401:
            raise SubmissionError("authentication failed; check the student token")
        if response.status_code == 403:
            raise SubmissionError("you are not authorized for this assignment")
        if response.status_code == 429:
            raise SubmissionError("submission rate limit reached; try again later")
        if response.status_code >= 400:
            raise SubmissionError(f"receiver rejected the submission (HTTP {response.status_code})")
        try:
            result = response.json()
            receipt_id = result["receipt_id"]
            submitted_at = result["submitted_at"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SubmissionError("receiver returned an invalid response") from exc
        if not isinstance(receipt_id, str) or not isinstance(submitted_at, str):
            raise SubmissionError("receiver returned an invalid response")
        return {"receipt_id": receipt_id, "submitted_at": submitted_at}
