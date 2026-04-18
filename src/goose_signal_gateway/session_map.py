"""
Persistent mapping from Signal conversation → goosed session ID.

Backed by a JSON file. Atomic writes (tmp + rename). Loaded once at startup,
kept in memory, flushed on every change.

File is written mode 0o600 — contains phone numbers and session IDs.
"""

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ConversationKey:
    kind: Literal["dm", "group"]
    identifier: str  # E.164 for DM; group_id bytes-as-hex for group

    def as_str(self) -> str:
        return f"{self.kind}:{self.identifier}"

    @classmethod
    def from_str(cls, s: str) -> "ConversationKey":
        kind, _, identifier = s.partition(":")
        if kind not in ("dm", "group"):
            raise ValueError(f"Unknown conversation kind: {kind!r}")
        return cls(kind=kind, identifier=identifier)  # type: ignore[arg-type]


class SessionMap:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._data: dict[str, str] = {}

    @classmethod
    async def load(cls, path: Path) -> "SessionMap":
        """Load from disk (or start empty if file doesn't exist)."""
        instance = cls(path)
        if path.exists():
            with open(path) as f:
                instance._data = json.load(f)
        return instance

    async def get(self, key: ConversationKey) -> str | None:
        async with self._lock:
            return self._data.get(key.as_str())

    async def set(self, key: ConversationKey, acp_session_id: str) -> None:
        async with self._lock:
            self._data[key.as_str()] = acp_session_id
            self._flush()

    async def delete(self, key: ConversationKey) -> None:
        async with self._lock:
            self._data.pop(key.as_str(), None)
            self._flush()

    async def all(self) -> dict[str, str]:
        async with self._lock:
            return dict(self._data)

    def _flush(self) -> None:
        """Atomic write: write to tmp file in same dir, then rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".session_map_")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
