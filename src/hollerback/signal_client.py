"""
Client for signal-cli running as HTTP daemon at 127.0.0.1:8080.

signal-cli daemon mode delivers inbound messages via SSE at GET /api/v1/events.
Outbound messages use JSON-RPC 2.0 POST at /api/v1/rpc.

NOTE: The JSON-RPC `receive` method does NOT work in daemon mode —
signal-cli returns: "Receive command cannot be used if messages are already
being received." Use subscribe() to stream events instead.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

SIGNAL_CLI_BASE = "http://127.0.0.1:8080"
SIGNAL_CLI_RPC = f"{SIGNAL_CLI_BASE}/api/v1/rpc"
SIGNAL_CLI_EVENTS = f"{SIGNAL_CLI_BASE}/api/v1/events"

log = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    sender: str       # phone number e.g. "+16125551234"
    text: str
    timestamp: int


class SignalClient:
    def __init__(self, account: str):
        """account: the Signal phone number this gateway is registered as."""
        self._account = account
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
        self._rpc_id = 0
        self.supports_edit: bool | None = None

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    async def _rpc(self, method: str, params: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        resp = await self._client.post(
            SIGNAL_CLI_RPC,
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()

    async def send(self, recipient: str, message: str) -> int:
        """Send a text message. Returns the sent timestamp."""
        result = await self._rpc(
            "send",
            {
                "account": self._account,
                "recipient": [recipient],
                "message": message,
            },
        )
        if "error" in result:
            raise RuntimeError(f"signal-cli send error: {result['error']}")
        return int(result.get("result", {}).get("timestamp", 0))

    async def edit_message(
        self, recipient: str, target_timestamp: int, new_text: str
    ) -> bool:
        """
        Edit a previously sent message. Returns True on success.

        Falls back gracefully if signal-cli doesn't implement editMessage
        (versions that lack the HTTP RPC method return -32601).
        Callers should send a new message when this returns False.
        """
        result = await self._rpc(
            "editMessage",
            {
                "account": self._account,
                "recipient": [recipient],
                "targetTimestamp": target_timestamp,
                "message": new_text,
            },
        )
        if "error" in result:
            code = result["error"].get("code") if isinstance(result["error"], dict) else None
            if code == -32601:
                if self.supports_edit is not False:
                    log.info("signal-cli editMessage not supported — streaming edits disabled")
                self.supports_edit = False
            else:
                log.warning("signal-cli editMessage error: %s", result["error"])
            return False
        self.supports_edit = True
        return True

    async def send_read_receipt(self, recipient: str, timestamps: list[int]) -> None:
        """Send a read receipt, causing the sender to see filled double ticks."""
        result = await self._rpc(
            "sendReceipt",
            {
                "account": self._account,
                "recipient": recipient,
                "type": "read",
                "target-timestamps": timestamps,
            },
        )
        if "error" in result:
            log.debug("sendReceipt error (non-fatal): %s", result["error"])

    async def send_typing(self, recipient: str, stop: bool = False) -> None:
        """Send or stop a typing indicator to a recipient."""
        params: dict = {
            "account": self._account,
            "recipient": [recipient],
        }
        if stop:
            params["stop"] = True
        result = await self._rpc("sendTyping", params)
        if "error" in result:
            log.debug("signal-cli sendTyping error (non-fatal): %s", result["error"])

    async def subscribe(self) -> AsyncIterator[IncomingMessage]:
        """
        Subscribe to the SSE event stream and yield inbound text messages.

        Connects to GET /api/v1/events and parses `event:receive` events.
        Filters to only text messages (dataMessage with non-empty message body).
        Skips receipts, typing indicators, and other envelope types.
        """
        async with self._client.stream("GET", SIGNAL_CLI_EVENTS) as resp:
            resp.raise_for_status()
            event_type = None
            lines = resp.aiter_lines().__aiter__()
            while True:
                try:
                    line = await asyncio.wait_for(lines.__anext__(), timeout=120)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    raise TimeoutError("SSE stream idle for 120 seconds — no data received")
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    if event_type != "receive":
                        event_type = None
                        continue
                    try:
                        payload = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        event_type = None
                        continue

                    env = payload.get("envelope", {})
                    data_msg = env.get("dataMessage", {})
                    text = data_msg.get("message", "")
                    sender = env.get("sourceNumber") or env.get("source", "")
                    ts = env.get("timestamp", 0)

                    if text and sender and sender != self._account:
                        yield IncomingMessage(sender=sender, text=text, timestamp=ts)

                    event_type = None
                elif line == "":
                    event_type = None

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
