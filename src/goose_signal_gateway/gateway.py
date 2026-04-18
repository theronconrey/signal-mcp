"""
Main gateway loop.

Signal SSE stream → ACP session → streamed reply → Signal (with live edits).
"""

import asyncio
import logging
import time
from pathlib import Path

from .acp_client import AcpClient, AcpStreamInterruptedError
from .approvals import ApprovalCoordinator
from .dedup import MessageDeduplicator
from .goosed_client import discover_goosed
from .pairing import (
    ALREADY_PENDING_MESSAGE,
    PAIRING_MESSAGE_TEMPLATE,
    PairingStore,
)
from .session_map import ConversationKey, SessionMap
from .signal_client import IncomingMessage, SignalClient

log = logging.getLogger(__name__)

_STATE = Path.home() / ".local" / "share" / "goose-signal-gateway"
DEFAULT_SESSION_MAP_PATH = _STATE / "sessions.json"
DEFAULT_PAIRING_PATH = _STATE / "pairing.json"

# Streaming cadence defaults

class Gateway:
    def __init__(
        self,
        signal_account: str,
        session_map_path: Path = DEFAULT_SESSION_MAP_PATH,
        pairing_path: Path = DEFAULT_PAIRING_PATH,
        pairing_enabled: bool = True,
        allowed_users: list[str] | None = None,
        code_ttl_minutes: int = 60,
        home_conversation: str | None = None,
    ):
        self._signal_account = signal_account
        self._session_map_path = session_map_path
        self._home_conversation = home_conversation

        self._pairing: PairingStore | None = None
        if pairing_enabled:
            from datetime import timedelta
            self._pairing = PairingStore(
                path=pairing_path,
                code_ttl=timedelta(minutes=code_ttl_minutes),
                allowed_users=allowed_users,
            )

        self._sessions: SessionMap | None = None
        self._conv_locks: dict[str, asyncio.Lock] = {}
        self._dedup = MessageDeduplicator()
        self._acp: AcpClient | None = None
        self._signal: SignalClient | None = None
        self._approvals: ApprovalCoordinator | None = None

        self._tasks: set[asyncio.Task] = set()
        self._accepting = True

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self):
        config = discover_goosed()
        log.info("Found goosed at port %d", config.port)

        self._acp = AcpClient(config)
        self._signal = SignalClient(self._signal_account)
        self._sessions = await SessionMap.load(self._session_map_path)
        self._approvals = ApprovalCoordinator(self._signal, self._acp)

        await self._acp.initialize()
        log.info("goosed healthy")

        if self._home_conversation:
            try:
                await self._signal.send(
                    self._home_conversation,
                    "goose-signal-gateway started.",
                )
            except Exception as e:
                log.warning("Failed to send startup notification: %s", e)

        await self._run_loop()

    async def stop(self):
        self._accepting = False
        if self._tasks:
            log.info("Draining %d in-flight conversation(s)...", len(self._tasks))
            await asyncio.gather(*self._tasks, return_exceptions=True)

        if self._home_conversation and self._signal:
            try:
                await self._signal.send(
                    self._home_conversation,
                    "goose-signal-gateway stopping.",
                )
            except Exception:
                pass

        if self._acp:
            await self._acp.close()
        if self._signal:
            await self._signal.close()

    # ── main loop ─────────────────────────────────────────────────────────────

    def _conv_lock(self, key: ConversationKey) -> asyncio.Lock:
        k = key.as_str()
        if k not in self._conv_locks:
            self._conv_locks[k] = asyncio.Lock()
        return self._conv_locks[k]

    async def _run_loop(self):
        log.info("Gateway running. Subscribed to Signal SSE stream.")
        while True:
            try:
                async for msg in self._signal.subscribe():
                    if not self._accepting:
                        break
                    task = asyncio.create_task(self._handle(msg))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
            except Exception as e:
                log.warning("SSE stream error: %s — reconnecting in 5s", e)
                await asyncio.sleep(5)

    # ── per-message dispatch ──────────────────────────────────────────────────

    async def _handle(self, msg: IncomingMessage):
        sender = msg.sender
        text = msg.text.strip()

        if await self._dedup.seen(msg.timestamp, text, sender):
            log.debug("Dropping duplicate from %s ts=%d", sender, msg.timestamp)
            return

        log.info("Signal ← %s: %r", sender, text[:80])

        key = ConversationKey(kind="dm", identifier=sender)

        # Pending approval replies take priority
        if self._approvals and await self._approvals.handle_reply(key, text):
            return

        if self._pairing and not self._pairing.is_approved(sender):
            code = self._pairing.request_code(sender)
            if code is None:
                reply = ALREADY_PENDING_MESSAGE
            else:
                ttl_min = int(self._pairing._ttl // 60)
                reply = PAIRING_MESSAGE_TEMPLATE.format(code=code, ttl_minutes=ttl_min)
                log.info("Pairing code %s issued for %s", code, sender)
            await self._signal.send(sender, reply)
            return

        await self._signal.send_read_receipt(sender, [msg.timestamp])

        async with self._conv_lock(key):
            await self._run_conversation(key, text)

    # ── conversation handler ──────────────────────────────────────────────────

    async def _run_conversation(self, key: ConversationKey, text: str):
        sender = key.identifier

        session_id = await self._sessions.get(key)
        if session_id is None:
            session_id = await self._acp.session_new(
                cwd=str(Path.home()),
                metadata={
                    "source": "signal",
                    "source_conversation": key.as_str(),
                    "display_name": f"Signal: {sender}",
                },
            )
            await self._sessions.set(key, session_id)
            log.info("Created session %s for %s", session_id, sender)

        await self._signal.send_typing(sender)

        buffer = ""

        try:
            async for notif in self._acp.session_prompt(session_id, text):
                if notif.kind == "agent_message_chunk":
                    for part in notif.payload.get("content", []):
                        if part.get("type") == "text":
                            buffer += part["text"]

                elif notif.kind == "permission_request":
                    tool_name = notif.payload.get("tool", "unknown")
                    arguments = notif.payload.get("arguments", {})
                    request_id = notif.payload.get("id", "")
                    log.info("Permission request for %s: %s", session_id, tool_name)
                    await self._approvals.request(
                        session_id=session_id,
                        request_id=request_id,
                        signal_conversation=key,
                        tool_name=tool_name,
                        arguments=arguments,
                    )

                elif notif.kind == "session_complete":
                    break

        except AcpStreamInterruptedError as e:
            log.error("Stream interrupted for session %s: %s", session_id, e)
            await self._signal.send(sender, "(Connection to Goose lost — please try again)")
            await self._signal.send_typing(sender, stop=True)
            return

        final = buffer.strip() or "(no reply)"
        if final == "(no reply)":
            log.warning("Empty reply for session %s", session_id)
        else:
            log.info("Signal → %s: %r", sender, final[:80])

        await self._signal.send(sender, final)

        await self._signal.send_typing(sender, stop=True)
