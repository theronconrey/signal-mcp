"""
Message deduplication.

Signal can deliver the same message more than once (reconnects, retries).
This module tracks seen message fingerprints and filters duplicates.
"""

import asyncio
import hashlib
import time
from collections import OrderedDict


class MessageDeduplicator:
    def __init__(self, ttl_seconds: int = 60, max_entries: int = 1000) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()

    def _key(self, timestamp: int, text: str, source: str) -> str:
        return hashlib.sha256(f"{timestamp}:{text}:{source}".encode()).hexdigest()

    def _prune(self, now: float) -> None:
        cutoff = now - self._ttl
        while self._seen:
            oldest_key, oldest_ts = next(iter(self._seen.items()))
            if oldest_ts < cutoff:
                del self._seen[oldest_key]
            else:
                break
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)

    async def seen(self, timestamp: int, text: str, source: str) -> bool:
        """Return True if this message has been seen before; record it if not."""
        key = self._key(timestamp, text, source)
        now = time.monotonic()
        async with self._lock:
            self._prune(now)
            if key in self._seen:
                return True
            self._seen[key] = now
            return False

    async def remember_outbound(self, timestamp: int, text: str, our_account: str) -> None:
        """Record an outbound message so we can ignore its echo if Signal reflects it back."""
        key = self._key(timestamp, text, our_account)
        now = time.monotonic()
        async with self._lock:
            self._prune(now)
            self._seen[key] = now
