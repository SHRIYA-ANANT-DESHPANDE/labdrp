"""LabDrop command-line entry point."""

from getpass import getpass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from labdrop import __version__
from labdrop.cli.client.submissions import (
    SubmissionError,
    receiver_url,
    request_student,
    save_receiver_url,
    submit_plan,
    validate_receiver_url,
)
from labdrop.cli.packager import PackageError, plan_package, write_package
from labdrop.cli.recorder.linux import LinuxRecorder, RecorderError

app = typer.Typer(no_args_is_help=True, help="Record a lab terminal session.")
console = Console(stderr=True)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=lambda value: _show_version(value), is_eager=True, help="Show version and exit."),
    ] = False,
) -> None:
    """LabDrop student commands."""


def _show_version(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.command()
def capture(
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help="Path where the readable terminal log will be saved.",
            dir_okay=False,
            writable=True,
        ),
    ] = Path("terminal_log.txt"),
) -> None:
    """Open an interactive shell and record its visible terminal output."""
    try:
        recorder = LinuxRecorder()
        transcript = recorder.run()
        output.expanduser().resolve().write_text(transcript, encoding="utf-8")
    except (OSError, RecorderError) as exc:
        console.print(f"[red]Could not record terminal session:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"\n[green]Terminal log saved to[/green] {output.expanduser().resolve()}")


@app.command()
def package(
    directory: Annotated[Path, typer.Argument(help="Assignment directory to package.")],
    assignment: Annotated[str, typer.Option("--assignment", help="Assignment identifier.")],
    log: Annotated[Path | None, typer.Option("--log", help="Use this terminal log instead.")] = None,
    output: Annotated[Path | None, typer.Option("--output", help="ZIP path (must not exist).")] = None,
) -> None:
    """Preview and create a local ZIP package; this command does not upload it."""
    try:
        plan = plan_package(directory, assignment, log_path=log)
        target = output or Path.cwd() / f"{plan.root.name or 'assignment'}-submission.zip"
        console.print("[bold]LabDrop Package Preview[/bold]")
        console.print(f"Assignment: {plan.assignment_id}")
        console.print("\n[green]Included:[/green]")
        for path in plan.summary.included:
            console.print(f"  {path}", markup=False)
        console.print("\n[yellow]Excluded:[/yellow]")
        if plan.summary.excluded:
            for path, reason in plan.summary.excluded:
                console.print(f"  {path} ({reason})", markup=False)
        else:
            console.print("  None", markup=False)
        console.print(f"\nIncluded payload files: {len(plan.payloads)}")
        console.print("Generated: manifest.json")
        result = write_package(plan, target)
    except (OSError, PackageError) as exc:
        console.print(f"[red]Could not package assignment:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"\n[green]Package created at[/green] {result.archive_path}")


@app.command()
def submit(
    directory: Annotated[Path, typer.Argument(help="Assignment directory to submit.")],
    assignment: Annotated[str, typer.Option("--assignment", help="Assignment identifier.")],
    log: Annotated[Path | None, typer.Option("--log", help="Use this terminal log instead.")] = None,
) -> None:
    """Package and submit an assignment using a privately prompted student token."""
    try:
        plan = plan_package(directory, assignment, log_path=log)
    except (OSError, PackageError) as exc:
        console.print(f"[red]Could not prepare submission:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print("[bold]LabDrop Submission Preview[/bold]")
    console.print(f"Assignment: {plan.assignment_id}")
    console.print("\n[green]Included:[/green]")
    for path in plan.summary.included:
        console.print(f"  {path}", markup=False)
    console.print("\n[yellow]Excluded:[/yellow]")
    for path, reason in plan.summary.excluded:
        console.print(f"  {path} ({reason})", markup=False)
    if not plan.summary.excluded:
        console.print("  None")

    if not typer.confirm("Continue with submission?", default=False):
        console.print("Submission cancelled.")
        return

    token = getpass("Student token: ")
    try:
        result = submit_plan(plan, token)
    except SubmissionError as exc:
        console.print(f"[red]Submission failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"\n[green]Receipt:[/green] {result['receipt_id']}")
    console.print(f"Server timestamp: {result['submitted_at']}")


@app.command()
def history() -> None:
    """List the authenticated student's submissions."""
    token = getpass("Student token: ")
    try:
        items = request_student("/submissions", token)
    except SubmissionError as exc:
        console.print(f"[red]History failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if not isinstance(items, list):
        console.print("[red]Receiver returned an invalid response.[/red]")
        raise typer.Exit(code=1)
    if not items:
        console.print("No submissions found.")
        return
    for item in items:
        console.print(f"{item['receipt_id']}  {item['assignment_id']}  {item['submitted_at']}")


@app.command()
def verify(receipt_id: str = typer.Argument(...)) -> None:
    """Verify an owned receipt and the stored archive checksum."""
    token = getpass("Student token: ")
    try:
        result = request_student(f"/submissions/{receipt_id}", token)
    except SubmissionError as exc:
        console.print(f"[red]Verification failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if not isinstance(result, dict):
        console.print("[red]Receiver returned an invalid response.[/red]")
        raise typer.Exit(code=1)
    console.print(f"Receipt: {result['receipt_id']}")
    console.print(f"Assignment: {result['assignment_id']}")
    console.print(f"Server timestamp: {result['submitted_at']}")
    console.print(f"Integrity: {result['integrity']}")


@app.command()
def configure() -> None:
    """Save a non-secret receiver URL."""
    value = typer.prompt("Receiver URL", default=receiver_url())
    try:
        path = save_receiver_url(validate_receiver_url(value))
    except (OSError, SubmissionError) as exc:
        console.print(f"[red]Configuration failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Receiver URL saved to {path}")


@app.command()
def status() -> None:
    """Check receiver connectivity and display non-secret configuration."""
    url = receiver_url()
    try:
        import httpx

        response = httpx.get(f"{url}/health", timeout=5)
        response.raise_for_status()
    except Exception as exc:
        console.print(f"Receiver: unavailable ({url})")
        raise typer.Exit(code=1) from exc
    console.print(f"Receiver: available ({url})")


if __name__ == "__main__":
    app()
