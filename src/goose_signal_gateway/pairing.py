"""
Pairing handshake for unknown Signal senders.

Unknown senders receive a one-time code; an operator must run
`goose-signal pairing approve <code>` before the bot will process
their messages. Approved senders are persisted across restarts.
"""

import json
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path


@dataclass
class PendingCode:
    code: str
    source: str       # E.164 phone number
    issued_at: float  # time.time()
    expires_at: float


class PairingStore:
    def __init__(
        self,
        path: Path,
        code_ttl: timedelta = timedelta(minutes=60),
        max_pending: int = 3,
        allowed_users: list[str] | None = None,
    ) -> None:
        self._path = path
        self._ttl = code_ttl.total_seconds()
        self._max_pending = max_pending
        self._approved: set[str] = set(allowed_users or [])
        self._pending: dict[str, PendingCode] = {}  # code → PendingCode
        self._load()

    # ── public API ────────────────────────────────────────────────────────────

    def is_approved(self, source: str) -> bool:
        self._expire()
        return source in self._approved

    def request_code(self, source: str) -> str | None:
        """
        Issue a new pairing code for source.

        Returns the code, or None if source already has a pending code
        (caller should send the "already issued" reply).
        """
        self._expire()
        existing = self._pending_for(source)
        if existing:
            return None

        if len(self._pending) >= self._max_pending:
            # Evict oldest to stay within cap
            oldest = min(self._pending.values(), key=lambda p: p.issued_at)
            del self._pending[oldest.code]

        code = _generate_code()
        now = time.time()
        self._pending[code] = PendingCode(
            code=code,
            source=source,
            issued_at=now,
            expires_at=now + self._ttl,
        )
        self._flush()
        return code

    def approve(self, code: str) -> str | None:
        """
        Approve the sender associated with code. Returns the source, or None if
        the code is unknown or expired.
        """
        self._expire()
        pending = self._pending.pop(code.upper(), None)
        if pending is None:
            return None
        self._approved.add(pending.source)
        self._flush()
        return pending.source

    def list_pending(self) -> list[PendingCode]:
        self._expire()
        return sorted(self._pending.values(), key=lambda p: p.issued_at)

    def deny(self, code: str) -> bool:
        """Remove a pending code without approving. Returns True if found."""
        self._expire()
        if code.upper() in self._pending:
            del self._pending[code.upper()]
            self._flush()
            return True
        return False

    def revoke_approval(self, source: str) -> bool:
        """Remove an approved sender. Returns True if they were approved."""
        if source in self._approved:
            self._approved.discard(source)
            self._flush()
            return True
        return False

    # ── internals ─────────────────────────────────────────────────────────────

    def _pending_for(self, source: str) -> PendingCode | None:
        for p in self._pending.values():
            if p.source == source:
                return p
        return None

    def _expire(self) -> None:
        now = time.time()
        expired = [c for c, p in self._pending.items() if p.expires_at <= now]
        if expired:
            for c in expired:
                del self._pending[c]
            self._flush()

    def _load(self) -> None:
        if not self._path.exists():
            return
        with open(self._path) as f:
            data = json.load(f)
        self._approved.update(data.get("approved", []))
        for p in data.get("pending", []):
            pc = PendingCode(**p)
            self._pending[pc.code] = pc

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "approved": sorted(self._approved),
            "pending": [asdict(p) for p in self._pending.values()],
        }
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".pairing_")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _generate_code() -> str:
    return secrets.token_urlsafe(4).upper()[:6]


PAIRING_MESSAGE_TEMPLATE = (
    "This bot requires pairing. Ask the operator to run:\n\n"
    "    goose-signal pairing approve {code}\n\n"
    "Your pairing code expires in {ttl_minutes} minutes."
)

ALREADY_PENDING_MESSAGE = (
    "A pairing code was already issued for your number. "
    "Ask the operator to run `goose-signal pairing list` to find it."
)
