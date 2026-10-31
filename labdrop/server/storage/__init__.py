"""Storage adapter contract for accepted submission archives."""

from pathlib import Path
from typing import Protocol


class StorageAdapter(Protocol):
    """Minimal interface implemented by submission archive stores."""

    def store(self, source: Path, receipt_id: str) -> Path:
        """Persist a validated archive without replacing an existing one."""
