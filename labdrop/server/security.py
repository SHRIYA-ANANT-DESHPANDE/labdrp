"""Token verification and assignment authorization dependencies."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from labdrop.server.database import connect

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class StudentIdentity:
    student_id: int
    roll: str
    course: str


def authenticate_student(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> StudentIdentity:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token_hash = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
    with connect() as connection:
        row = connection.execute(
            """SELECT s.id, s.roll, s.course, t.expires_at, t.revoked_at
               FROM tokens t JOIN students s ON s.id = t.student_id
               WHERE t.token_hash = ?""",
            (token_hash,),
        ).fetchone()
    if row is None or row["revoked_at"] is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if row["expires_at"]:
        expiration = datetime.fromisoformat(row["expires_at"])
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=timezone.utc)
        if expiration <= datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return StudentIdentity(row["id"], row["roll"], row["course"])


def require_assignment_access(assignment_id: str, identity: StudentIdentity) -> None:
    with connect() as connection:
        row = connection.execute(
            """SELECT 1 FROM student_assignments sa
               JOIN assignments a ON a.id = sa.assignment_id
               WHERE sa.student_id = ? AND a.assignment_id = ? AND a.course = ?""",
            (identity.student_id, assignment_id, identity.course),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Assignment access denied")
