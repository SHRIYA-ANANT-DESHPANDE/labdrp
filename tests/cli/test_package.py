"""CLI preview tests for local package creation."""

from pathlib import Path

from typer.testing import CliRunner

from labdrop.cli.app import app


def test_package_command_shows_included_excluded_and_writes_zip(tmp_path: Path) -> None:
    assignment = tmp_path / "Lab05"
    assignment.mkdir()
    (assignment / "program.c").write_text("int main(void) {}\n", encoding="utf-8")
    (assignment / "terminal_log.txt").write_text("log", encoding="utf-8")
    (assignment / "a.out").write_bytes(b"\x7fELF\0")
    output = tmp_path / "result.zip"

    result = CliRunner().invoke(
        app,
        ["package", str(assignment), "--assignment", "OS-Lab-05", "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert "program.c" in result.output
    assert "terminal_log.txt" in result.output
    assert "a.out" in result.output
    assert "manifest.json" in result.output
    assert output.is_file()

