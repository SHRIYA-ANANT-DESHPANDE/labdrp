"""SQLite persistence for instructor provisioning and student authorization."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    roll TEXT NOT NULL UNIQUE CHECK(length(trim(roll)) > 0),
    course TEXT NOT NULL CHECK(length(trim(course)) > 0),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE CHECK(length(token_hash) = 64),
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_student ON tokens(student_id);
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY,
    assignment_id TEXT NOT NULL UNIQUE CHECK(length(trim(assignment_id)) > 0),
    course TEXT NOT NULL CHECK(length(trim(course)) > 0),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS student_assignments (
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY(student_id, assignment_id)
);
CREATE INDEX IF NOT EXISTS idx_permissions_assignment ON student_assignments(assignment_id);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_action_created ON audit_events(action, created_at);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE,
    student_id INTEGER NOT NULL REFERENCES students(id),
    assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id),
    stored_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    submitted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_student ON submissions(student_id, submitted_at);
CREATE TABLE IF NOT EXISTS upload_rate (
    student_id INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
    window_start REAL NOT NULL,
    request_count INTEGER NOT NULL CHECK(request_count >= 0)
);
"""


def database_path() -> Path:
    configured = os.environ.get("LABDROP_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "labdrop" / "admin.sqlite3"


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = (path or database_path()).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(db_path, timeout=10, isolation_level="IMMEDIATE")
    if os.name == "posix":
        db_path.chmod(0o600)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.executescript(SCHEMA)
    return connection


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit(connection: sqlite3.Connection, action: str, details: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO audit_events(actor, action, details_json, created_at) VALUES (?, ?, ?, ?)",
        ("local-admin", action, json.dumps(details, sort_keys=True), now_iso()),
    )
