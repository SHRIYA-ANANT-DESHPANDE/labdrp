"""Behavior tests for assignment scanning and ZIP generation."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from labdrop.cli.packager import PackageError, create_package, plan_package


def test_package_preserves_paths_and_manifest_matches_archive(tmp_path: Path) -> None:
    assignment = tmp_path / "Lab05"
    (assignment / "src").mkdir(parents=True)
    (assignment / "empty").mkdir()
    (assignment / "src" / "program.c").write_text("int main(void) {}\n", encoding="utf-8")
    (assignment / "terminal_log.txt").write_text("run output\n", encoding="utf-8")
    (assignment / "a.out").write_bytes(b"\x7fELF\0binary")
    (assignment / "src" / "module.o").write_bytes(b"object")
    (assignment / ".venv").mkdir()
    (assignment / ".venv" / "secret.txt").write_text("exclude", encoding="utf-8")
    archive_path = assignment / "bundle.zip"

    result = create_package(assignment, "OS-Lab-05", output_path=archive_path, cwd=tmp_path)

    assert ("a.out", "compiled binary or system file") in result.summary.excluded
    assert any(path == ".venv/" for path, _ in result.summary.excluded)
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {
            "src/program.c", "terminal_log.txt", "manifest.json"
        }
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["assignment_id"] == "OS-Lab-05"
        assert {entry["path"] for entry in manifest["files"]} == {
            "src/program.c", "terminal_log.txt"
        }
        for entry in manifest["files"]:
            content = archive.read(entry["path"])
            assert entry["size"] == len(content)
            assert entry["sha256"] == hashlib.sha256(content).hexdigest()


def test_log_precedence_and_explicit_override(tmp_path: Path) -> None:
    assignment = tmp_path / "Lab"
    assignment.mkdir()
    (assignment / "terminal_log.txt").write_text("assignment log", encoding="utf-8")
    cwd_log = tmp_path / "terminal_log.txt"
    cwd_log.write_text("cwd log", encoding="utf-8")
    explicit = tmp_path / "chosen.txt"
    explicit.write_text("explicit log", encoding="utf-8")

    plan = plan_package(assignment, "A", cwd=tmp_path)
    assert next(p.source_path for p in plan.payloads if p.archive_path == "terminal_log.txt") == (
        assignment / "terminal_log.txt"
    )
    plan = plan_package(assignment, "A", log_path=explicit, cwd=tmp_path)
    assert next(p.source_path for p in plan.payloads if p.archive_path == "terminal_log.txt") == explicit

    (assignment / "terminal_log.txt").unlink()
    plan = plan_package(assignment, "A", cwd=tmp_path)
    assert next(p.source_path for p in plan.payloads if p.archive_path == "terminal_log.txt") == cwd_log


def test_missing_log_and_existing_output_fail_without_overwrite(tmp_path: Path) -> None:
    assignment = tmp_path / "Lab"
    assignment.mkdir()
    (assignment / "program.c").write_text("source", encoding="utf-8")
    with pytest.raises(PackageError, match="terminal_log.txt was not found"):
        plan_package(assignment, "A", cwd=tmp_path)

    (assignment / "terminal_log.txt").write_text("log", encoding="utf-8")
    destination = tmp_path / "existing.zip"
    destination.write_bytes(b"keep me")
    with pytest.raises(PackageError, match="refusing to overwrite"):
        create_package(assignment, "A", output_path=destination, cwd=tmp_path)
    assert destination.read_bytes() == b"keep me"


def test_symlinks_and_source_manifest_collision_are_handled(tmp_path: Path) -> None:
    assignment = tmp_path / "Lab"
    assignment.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (assignment / "link.txt").symlink_to(outside)
    (assignment / "terminal_log.txt").write_text("log", encoding="utf-8")
    (assignment / "manifest.json").write_text("student file", encoding="utf-8")

    result = create_package(assignment, "A", cwd=tmp_path)
    assert any(path == "link.txt" for path, _ in result.summary.excluded)
    assert any(path == "manifest.json" for path, _ in result.summary.excluded)
    with zipfile.ZipFile(result.archive_path) as archive:
        assert "outside.txt" not in archive.namelist()
        assert archive.namelist().count("manifest.json") == 1
