"""Authenticated, bounded ZIP intake and owner-only receipt verification."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from labdrop.server.database import audit, connect, now_iso
from labdrop.server.security import StudentIdentity, authenticate_student, require_assignment_access
from labdrop.server.storage.local import LocalStorageAdapter, storage_root

router = APIRouter()
storage = LocalStorageAdapter()
MAX_ARCHIVE = 25 * 1024 * 1024
MAX_UNCOMPRESSED = 100 * 1024 * 1024
MAX_FILE = 10 * 1024 * 1024
MAX_FILES = 200
MAX_RATIO = 100
RATE_WINDOW = 60
RATE_LIMIT = 10


@router.post("/submissions/{assignment_id}", status_code=status.HTTP_201_CREATED)
def upload_submission(
    assignment_id: str,
    file: Annotated[UploadFile, File(...)],
    identity: Annotated[StudentIdentity, Depends(authenticate_student)],
) -> dict[str, str]:
    require_assignment_access(assignment_id, identity)
    _check_rate(identity)
    fd, temp_name = tempfile.mkstemp(prefix="labdrop-upload-")
    os.close(fd)
    temporary = Path(temp_name)
    try:
        size, archive_hash = _save_upload(file, temporary)
        _validate_archive(temporary, assignment_id)
        receipt = _new_receipt()
        stored = storage.store(temporary, receipt)
        submitted = now_iso()
        try:
            with connect() as db:
                db.execute(
                    """INSERT INTO submissions(receipt_id, student_id, assignment_id, stored_path,
                       sha256, size_bytes, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (receipt, identity.student_id, assignment_id, str(stored), archive_hash, size, submitted),
                )
                audit(db, "submission.accept", {"receipt_id": receipt, "assignment": assignment_id})
        except Exception:
            stored.unlink(missing_ok=True)
            raise
        return {"receipt_id": receipt, "assignment_id": assignment_id, "submitted_at": submitted}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="invalid ZIP archive") from exc
    finally:
        temporary.unlink(missing_ok=True)
        file.file.close()


@router.get("/submissions/{receipt_id}")
def verify_submission(
    receipt_id: str,
    identity: Annotated[StudentIdentity, Depends(authenticate_student)],
) -> dict[str, str | int]:
    with connect() as db:
        row = db.execute(
            "SELECT receipt_id, assignment_id, sha256, size_bytes, submitted_at, stored_path FROM submissions "
            "WHERE receipt_id = ? AND student_id = ?",
            (receipt_id, identity.student_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    archive_path = Path(row["stored_path"]).resolve()
    root = storage_root().resolve()
    if not archive_path.is_relative_to(root) or not archive_path.is_file():
        raise HTTPException(status_code=409, detail="Submission integrity check failed")
    digest = hashlib.sha256()
    with archive_path.open("rb") as archive:
        while chunk := archive.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != row["sha256"]:
        raise HTTPException(status_code=409, detail="Submission integrity check failed")
    return {**dict(row), "integrity": "verified"}


@router.get("/submissions")
def submission_history(
    identity: Annotated[StudentIdentity, Depends(authenticate_student)],
) -> list[dict[str, str | int]]:
    with connect() as db:
        rows = db.execute(
            "SELECT receipt_id, assignment_id, sha256, size_bytes, submitted_at FROM submissions "
            "WHERE student_id = ? ORDER BY submitted_at DESC",
            (identity.student_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _save_upload(upload: UploadFile, target: Path) -> tuple[int, str]:
    total = 0
    digest = hashlib.sha256()
    with target.open("wb") as destination:
        while chunk := upload.file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ARCHIVE:
                raise ValueError("compressed archive exceeds size limit")
            digest.update(chunk)
            destination.write(chunk)
    return total, digest.hexdigest()


def _validate_archive(path: Path, assignment_id: str) -> None:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names: set[str] = set()
        payload: dict[str, tuple[int, str]] = {}
        if len(infos) > MAX_FILES + 1:
            raise ValueError("archive contains too many entries")
        total = 0
        manifest_bytes = None
        for info in infos:
            name = info.filename
            if name in names:
                raise ValueError("archive contains duplicate paths")
            names.add(name)
            if not _safe_zip_path(name) or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("archive contains an unsafe path or symlink")
            if info.is_dir():
                raise ValueError("directory entries are not supported")
            if info.file_size > MAX_FILE:
                raise ValueError("archive contains a file above the size limit")
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise ValueError("archive exceeds uncompressed size limit")
            if info.file_size and (info.compress_size == 0 or info.file_size / info.compress_size > MAX_RATIO):
                raise ValueError("archive compression ratio exceeds limit")
            digest = hashlib.sha256()
            size = 0
            with archive.open(info) as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE or total - info.file_size + size > MAX_UNCOMPRESSED:
                        raise ValueError("archive exceeds actual content size limit")
                    digest.update(chunk)
                    if name == "manifest.json":
                        manifest_bytes = (manifest_bytes or b"") + chunk
            if size != info.file_size:
                raise ValueError("archive file size does not match ZIP metadata")
            if name != "manifest.json":
                payload[name] = (size, digest.hexdigest())

    if manifest_bytes is None:
        raise ValueError("manifest.json is missing")
    try:
        manifest = json.loads(manifest_bytes)
        if (
            manifest["format"] != "labdrop-submission"
            or manifest["format_version"] != 1
            or manifest["assignment_id"] != assignment_id
            or not isinstance(manifest["files"], list)
        ):
            raise ValueError
        listed: dict[str, tuple[int, str]] = {}
        for item in manifest["files"]:
            name, size, digest = item["path"], item["size"], item["sha256"]
            if not isinstance(name, str) or not _safe_zip_path(name) or name == "manifest.json":
                raise ValueError
            if not isinstance(size, int) or size < 0 or not isinstance(digest, str) or len(digest) != 64:
                raise ValueError
            if name in listed:
                raise ValueError
            listed[name] = (size, digest.lower())
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("manifest is invalid") from exc
    if listed != payload:
        raise ValueError("manifest files or checksums do not match archive contents")


def _safe_zip_path(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and "\\" not in name and not path.is_absolute() and all(
        part not in ("", ".", "..") for part in path.parts
    ) and not (len(name) > 1 and name[1] == ":")


def _check_rate(identity: StudentIdentity) -> None:
    now = time.time()
    with connect() as db:
        row = db.execute("SELECT window_start, request_count FROM upload_rate WHERE student_id = ?", (identity.student_id,)).fetchone()
        if row is None or now - row["window_start"] >= RATE_WINDOW:
            db.execute(
                "INSERT INTO upload_rate(student_id, window_start, request_count) VALUES (?, ?, 1) "
                "ON CONFLICT(student_id) DO UPDATE SET window_start=excluded.window_start, request_count=1",
                (identity.student_id, now),
            )
        elif row["request_count"] >= RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Upload rate limit exceeded")
        else:
            db.execute("UPDATE upload_rate SET request_count=request_count+1 WHERE student_id=?", (identity.student_id,))


def _new_receipt() -> str:
    year = datetime.now(timezone.utc).year
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return f"LD-{year}-" + "".join(secrets.choice(alphabet) for _ in range(8))
