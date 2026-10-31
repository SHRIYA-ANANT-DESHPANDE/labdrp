"""Local instructor provisioning commands; no provisioning HTTP routes exist."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import date, datetime, time, timezone

import typer
from rich.console import Console

from labdrop.server.database import audit, connect, now_iso

app = typer.Typer(no_args_is_help=True, help="Provision LabDrop students and permissions locally.")
console = Console()


def _required(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 200 or "\x00" in cleaned:
        raise typer.BadParameter(f"{name} must be 1 to 200 characters")
    return cleaned


@app.command("register-student")
def register_student(roll: str = typer.Option(...), course: str = typer.Option(...)) -> None:
    roll, course = _required(roll, "roll"), _required(course, "course")
    try:
        with connect() as db:
            cursor = db.execute(
                "INSERT INTO students(roll, course, created_at) VALUES (?, ?, ?)",
                (roll, course, now_iso()),
            )
            audit(db, "student.register", {"roll": roll, "course": course})
            student_id = cursor.lastrowid
    except sqlite3.IntegrityError as exc:
        raise typer.BadParameter("student already exists or input violates database constraints") from exc
    console.print(f"Registered student {roll} (id {student_id}) in {course}.")


@app.command("create-token")
def create_token(
    roll: str = typer.Option(...),
    expires: str | None = typer.Option(None, help="Optional expiry date YYYY-MM-DD (UTC)."),
) -> None:
    roll = _required(roll, "roll")
    expires_at = None
    if expires:
        try:
            expires_at = datetime.combine(date.fromisoformat(expires), time.max, timezone.utc).isoformat()
        except ValueError as exc:
            raise typer.BadParameter("expiry must use YYYY-MM-DD") from exc
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    try:
        with connect() as db:
            student = db.execute("SELECT id FROM students WHERE roll = ?", (roll,)).fetchone()
            if student is None:
                raise typer.BadParameter("student is not registered")
            db.execute(
                "INSERT INTO tokens(student_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (student["id"], token_hash, now_iso(), expires_at),
            )
            audit(db, "token.create", {"roll": roll, "expires_at": expires_at})
    except sqlite3.IntegrityError as exc:
        raise typer.BadParameter("token could not be created") from exc
    console.print("Token (shown once; store securely):")
    console.print(token, markup=False)


@app.command("revoke-token")
def revoke_token(roll: str = typer.Option(...)) -> None:
    roll = _required(roll, "roll")
    with connect() as db:
        student = db.execute("SELECT id FROM students WHERE roll = ?", (roll,)).fetchone()
        if student is None:
            raise typer.BadParameter("student is not registered")
        cursor = db.execute(
            "UPDATE tokens SET revoked_at = ? WHERE student_id = ? AND revoked_at IS NULL",
            (now_iso(), student["id"]),
        )
        if cursor.rowcount == 0:
            raise typer.BadParameter("student has no active tokens")
        audit(db, "token.revoke", {"roll": roll, "count": cursor.rowcount})
    console.print(f"Revoked {cursor.rowcount} active token(s) for {roll}.")


@app.command("create-assignment")
def create_assignment(
    assignment: str = typer.Option(..., help="Assignment identifier."),
    course: str = typer.Option(...),
) -> None:
    assignment = _required(assignment, "assignment")
    course = _required(course, "course")
    try:
        with connect() as db:
            db.execute(
                "INSERT INTO assignments(assignment_id, course, created_at) VALUES (?, ?, ?)",
                (assignment, course, now_iso()),
            )
            audit(db, "assignment.create", {"assignment": assignment, "course": course})
    except sqlite3.IntegrityError as exc:
        raise typer.BadParameter("assignment already exists or input violates database constraints") from exc
    console.print(f"Created assignment {assignment} for {course}.")


@app.command("assign-permission")
def assign_permission(
    roll: str = typer.Option(...),
    assignment: str = typer.Option(...),
) -> None:
    roll = _required(roll, "roll")
    assignment = _required(assignment, "assignment")
    try:
        with connect() as db:
            student = db.execute("SELECT id, course FROM students WHERE roll = ?", (roll,)).fetchone()
            target = db.execute(
                "SELECT id, course FROM assignments WHERE assignment_id = ?", (assignment,)
            ).fetchone()
            if student is None or target is None:
                raise typer.BadParameter("student or assignment is not registered")
            if student["course"] != target["course"]:
                raise typer.BadParameter("student and assignment courses do not match")
            db.execute(
                "INSERT INTO student_assignments(student_id, assignment_id, created_at) VALUES (?, ?, ?)",
                (student["id"], target["id"], now_iso()),
            )
            audit(db, "permission.assign", {"roll": roll, "assignment": assignment})
    except sqlite3.IntegrityError as exc:
        raise typer.BadParameter("permission already exists or input violates database constraints") from exc
    console.print(f"Granted {roll} access to {assignment}.")


if __name__ == "__main__":
    app()
