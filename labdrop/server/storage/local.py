"""Immutable local disk storage for validated ZIP submissions."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def storage_root() -> Path:
    return Path(os.environ.get("LABDROP_STORAGE_DIR", "~/.local/share/labdrop/submissions")).expanduser()


class LocalStorageAdapter:
    """Store archives on local disk with atomic no-overwrite publication."""

    def store(self, source: Path, receipt_id: str) -> Path:
        root = storage_root()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix=".upload-", dir=root)
        os.close(fd)
        temporary = Path(name)
        target = root / f"{receipt_id}.zip"
        try:
            os.replace(source, temporary)
            os.link(temporary, target)
            return target
        finally:
            temporary.unlink(missing_ok=True)
