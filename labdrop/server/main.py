"""FastAPI identity and assignment authorization routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI

from labdrop.server.database import connect
from labdrop.server.security import StudentIdentity, authenticate_student, require_assignment_access
from labdrop.server.submissions import router as submissions_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    with connect():
        pass
    yield


app = FastAPI(title="LabDrop Receiver", version="0.1.0", lifespan=lifespan)
app.include_router(submissions_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/me")
def current_student(identity: Annotated[StudentIdentity, Depends(authenticate_student)]) -> dict[str, str]:
    return {"roll": identity.roll, "course": identity.course}


@app.get("/assignments/{assignment_id}/access")
def assignment_access(
    assignment_id: str,
    identity: Annotated[StudentIdentity, Depends(authenticate_student)],
) -> dict[str, str]:
    require_assignment_access(assignment_id, identity)
    return {"assignment_id": assignment_id, "access": "allowed"}
