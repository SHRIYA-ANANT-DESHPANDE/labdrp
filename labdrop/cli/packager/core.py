"""Build a ZIP and checksum manifest from an assignment directory."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

MANIFEST_NAME = "manifest.json"
LOG_NAME = "terminal_log.txt"
CHUNK_SIZE = 1024 * 1024
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
    "node_modules", ".tox", ".nox", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", ".hypothesis", ".cache", "build", "dist",
}
EXCLUDED_NAMES = {
    "a.out", ".ds_store", "thumbs.db", "desktop.ini",
}
EXCLUDED_SUFFIXES = {
    ".o", ".obj", ".exe", ".dll", ".so", ".dylib", ".class", ".pyc",
    ".pyo", ".a", ".lib", ".pyd", ".swp", ".swo", ".tmp", ".temp",
    ".bak", ".orig", ".rej",
}
EXCLUDED_PATTERNS = ("*~", ".#*", "#*#")


class PackageError(RuntimeError):
    """Raised when an assignment cannot be packaged safely."""


@dataclass(frozen=True)
class PackageSummary:
    """The paths and reasons shown in the local package preview."""

    included: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PackageResult:
    """Result of writing an assignment ZIP and its manifest."""

    archive_path: Path
    summary: PackageSummary
    file_count: int


@dataclass(frozen=True)
class PackagePlan:
    """A validated set of files ready to be previewed and archived."""

    assignment_id: str
    payloads: tuple[_Payload, ...]
    summary: PackageSummary
    root: Path


@dataclass(frozen=True)
class _Payload:
    archive_path: str
    source_path: Path


def create_package(
    assignment_dir: Path,
    assignment_id: str,
    *,
    log_path: Path | None = None,
    output_path: Path | None = None,
    cwd: Path | None = None,
) -> PackageResult:
    """Scan and atomically create a no-overwrite submission archive."""
    plan = plan_package(assignment_dir, assignment_id, log_path=log_path, cwd=cwd)
    work_dir = (cwd or Path.cwd()).expanduser().resolve()
    target = output_path or work_dir / f"{plan.root.name or 'assignment'}-submission.zip"
    if not target.is_absolute():
        target = work_dir / target
    target = target.expanduser().resolve(strict=False)
    return write_package(plan, target)


def plan_package(
    assignment_dir: Path,
    assignment_id: str,
    *,
    log_path: Path | None = None,
    cwd: Path | None = None,
) -> PackagePlan:
    """Resolve files and exclusions without writing an archive."""
    root = assignment_dir.expanduser().resolve()
    if not root.is_dir():
        raise PackageError(f"assignment directory does not exist or is not a directory: {root}")
    if not assignment_id.strip():
        raise PackageError("assignment identifier must not be empty")

    work_dir = (cwd or Path.cwd()).expanduser().resolve()
    selected_log = _select_log(root, work_dir, log_path)
    payloads, exclusions = _scan(root, selected_log)
    if not payloads:
        raise PackageError("assignment contains no readable files to package")
    paths = [item.archive_path for item in payloads]
    if len(paths) != len(set(paths)):
        raise PackageError("duplicate paths would be written to the archive")
    if MANIFEST_NAME in paths:
        raise PackageError(f"{MANIFEST_NAME} is reserved for the generated manifest")

    summary = PackageSummary(tuple(paths), tuple(exclusions))
    return PackagePlan(assignment_id.strip(), tuple(payloads), summary, root)


def write_package(plan: PackagePlan, output_path: Path) -> PackageResult:
    """Write a plan to a ZIP and publish it without replacing an existing file."""
    target = output_path.expanduser().resolve(strict=False)
    _write_archive(target, plan.assignment_id, list(plan.payloads))
    return PackageResult(target, plan.summary, len(plan.payloads))


def _select_log(root: Path, cwd: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        return _validate_log(candidate)

    for candidate in (root / LOG_NAME, cwd / LOG_NAME):
        if candidate.exists():
            return _validate_log(candidate)
    raise PackageError(
        f"{LOG_NAME} was not found in the assignment directory or current directory; "
        "provide it with --log PATH"
    )


def _validate_log(candidate: Path) -> Path:
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise PackageError(f"cannot access terminal log {candidate}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PackageError(f"terminal log must be a regular, non-symlink file: {candidate}")
    if not os.access(candidate, os.R_OK):
        raise PackageError(f"terminal log is not readable: {candidate}")
    return candidate.resolve()


def _scan(root: Path, selected_log: Path) -> tuple[list[_Payload], list[tuple[str, str]]]:
    payloads: list[_Payload] = []
    exclusions: list[tuple[str, str]] = []
    selected_rel = _relative_if_inside(selected_log, root)
    seen: set[str] = set()

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
        except OSError as exc:
            relative = _relative_if_inside(directory, root) or "."
            exclusions.append((relative, f"directory cannot be read: {exc.strerror or exc}"))
            return

        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                if entry.is_symlink():
                    exclusions.append((relative, "symbolic links are not followed or packaged"))
                elif entry.is_dir(follow_symlinks=False):
                    if (
                        entry.name.casefold() in SKIP_DIRS
                        or entry.name.casefold().endswith(".egg-info")
                    ):
                        exclusions.append((relative + "/", "excluded directory"))
                    else:
                        visit(path)
                elif entry.is_file(follow_symlinks=False):
                    if relative == MANIFEST_NAME:
                        exclusions.append((relative, "reserved for generated manifest"))
                        continue
                    if relative == LOG_NAME and selected_rel != LOG_NAME:
                        exclusions.append((relative, "replaced by selected terminal log"))
                        continue
                    if selected_rel == relative:
                        continue
                    reason = _exclusion_reason(path.name)
                    if reason:
                        exclusions.append((relative, reason))
                        continue
                    if _looks_binary(path):
                        exclusions.append((relative, "binary file"))
                        continue
                    if not _readable_regular_file(path):
                        exclusions.append((relative, "file cannot be read"))
                        continue
                    _add_payload(payloads, seen, relative, path)
                else:
                    exclusions.append((relative, "not a regular file or directory"))
            except OSError as exc:
                exclusions.append((relative, f"cannot inspect file: {exc.strerror or exc}"))

    visit(root)
    if selected_rel != LOG_NAME or not any(item.archive_path == LOG_NAME for item in payloads):
        _add_payload(payloads, seen, LOG_NAME, selected_log)

    payloads.sort(key=lambda item: item.archive_path.casefold())
    exclusions.sort(key=lambda item: item[0].casefold())
    return payloads, exclusions


def _relative_if_inside(path: Path, root: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _exclusion_reason(name: str) -> str | None:
    lowered = name.casefold()
    if lowered in EXCLUDED_NAMES:
        return "compiled binary or system file"
    if Path(lowered).suffix in EXCLUDED_SUFFIXES or ".so." in lowered:
        return "compiled binary, object, temporary, or editor file"
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in EXCLUDED_PATTERNS):
        return "temporary or editor file"
    return None


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            sample = source.read(8192)
    except OSError:
        return False
    if b"\0" in sample:
        return True
    if not sample:
        return False
    controls = sum(byte < 32 and byte not in (7, 8, 9, 10, 12, 13, 27) for byte in sample)
    return controls / len(sample) > 0.30


def _readable_regular_file(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            return stat.S_ISREG(os.fstat(source.fileno()).st_mode)
    except OSError:
        return False


def _add_payload(payloads: list[_Payload], seen: set[str], name: str, source: Path) -> None:
    normalized = PurePosixPath(name).as_posix()
    if normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
        raise PackageError(f"unsafe archive path: {name}")
    if normalized in seen:
        raise PackageError(f"duplicate archive path: {normalized}")
    seen.add(normalized)
    payloads.append(_Payload(normalized, source))


def _write_archive(target: Path, assignment_id: str, payloads: list[_Payload]) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        os.close(fd)
    except OSError as exc:
        raise PackageError(f"cannot prepare archive at {target}: {exc}") from exc

    temp_path = Path(temp_name)
    manifest_files: list[dict[str, object]] = []
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in payloads:
                size, digest = _write_payload(archive, item)
                manifest_files.append({"path": item.archive_path, "size": size, "sha256": digest})
            manifest = {
                "format": "labdrop-submission",
                "format_version": 1,
                "assignment_id": assignment_id,
                "files": manifest_files,
            }
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
        _publish_without_overwrite(temp_path, target)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageError(f"failed to create archive {target}: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _write_payload(archive: zipfile.ZipFile, item: _Payload) -> tuple[int, str]:
    info = item.source_path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PackageError(f"source changed to a non-regular file during packaging: {item.source_path}")
    resolved = item.source_path.resolve(strict=True)
    with archive.open(item.archive_path, "w") as destination, resolved.open("rb") as source:
        current = os.fstat(source.fileno())
        if not stat.S_ISREG(current.st_mode):
            raise PackageError(f"source is not a regular file: {item.source_path}")
        digest = hashlib.sha256()
        size = _copy_and_hash(source, destination, digest)
    return size, digest.hexdigest()


def _copy_and_hash(source: BinaryIO, destination: BinaryIO, digest: object) -> int:
    size = 0
    while chunk := source.read(CHUNK_SIZE):
        destination.write(chunk)
        digest.update(chunk)  # type: ignore[attr-defined]
        size += len(chunk)
    return size


def _publish_without_overwrite(temp_path: Path, target: Path) -> None:
    try:
        os.link(temp_path, target)
    except FileExistsError as exc:
        raise PackageError(f"refusing to overwrite existing archive: {target}") from exc
    except OSError as exc:
        raise PackageError(f"cannot publish archive without overwriting {target}: {exc}") from exc

