import asyncio
from collections import deque

_MAX_PER_CONTACT = 500


class MessageBuffer:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._store: dict[str, deque[dict]] = {}

    async def append(self, phone_number: str, text: str, timestamp: int) -> None:
        async with self._lock:
            if phone_number not in self._store:
                self._store[phone_number] = deque(maxlen=_MAX_PER_CONTACT)
            self._store[phone_number].append(
                {"phone_number": phone_number, "text": text, "timestamp": timestamp}
            )

    async def get(self, phone_number: str | None = None, since: int = 0) -> list[dict]:
        async with self._lock:
            if phone_number is not None:
                msgs = list(self._store.get(phone_number, []))
            else:
                msgs = [m for q in self._store.values() for m in q]
            if since:
                msgs = [m for m in msgs if m["timestamp"] > since]
            return msgs
