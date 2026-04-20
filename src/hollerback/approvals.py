"""
Signal-side approval flow for Goose permission requests.

When a session_prompt stream yields a permission_request notification, the
gateway delegates to ApprovalCoordinator, which sends a yes/no prompt to Signal
and waits for a reply before resolving the permission via ACP.

NOTE: goosed v1.30.0 does not surface permission_request events via /reply and
does not implement resolve_permission. This module is fully wired for when that
support lands. resolve_permission() calls are silently skipped in the interim.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from .acp_client import AcpClient
from .session_map import ConversationKey
from .signal_client import SignalClient

log = logging.getLogger(__name__)

_APPROVAL_PROMPT = (
    "\u26a0\ufe0f  Goose wants to run:\n"
    "    {tool_name}\n"
    "    {args_summary}\n\n"
    'Reply "yes" to approve, "no" to deny.\n'
    "Expires in {timeout_minutes} minutes."
)
_TIMEOUT_MESSAGE = "Approval timed out — Goose request denied automatically."
_WAITING_MESSAGE = 'Waiting for "yes" or "no".'
_DESKTOP_MESSAGE = "This permission was answered in Goose Desktop."


@dataclass
class _Pending:
    session_id: str
    request_id: str
    future: asyncio.Future


class ApprovalCoordinator:
    def __init__(
        self,
        signal: SignalClient,
        acp: AcpClient,
        timeout: timedelta = timedelta(minutes=30),
    ) -> None:
        self._signal = signal
        self._acp = acp
        self._timeout = timeout.total_seconds()
        # One pending approval per conversation key
        self._pending: dict[str, _Pending] = {}

    async def request(
        self,
        session_id: str,
        request_id: str,
        signal_conversation: ConversationKey,
        tool_name: str,
        arguments: dict,
    ) -> bool:
        """
        Send approval prompt to Signal, await reply, resolve via ACP.
        Returns True if approved, False if denied or timed out.
        """
        conv_key = signal_conversation.as_str()
        args_summary = _summarise_args(arguments)
        timeout_minutes = int(self._timeout // 60)

        prompt = _APPROVAL_PROMPT.format(
            tool_name=tool_name,
            args_summary=args_summary,
            timeout_minutes=timeout_minutes,
        )
        await self._signal.send(signal_conversation.identifier, prompt)

        future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        self._pending[conv_key] = _Pending(
            session_id=session_id,
            request_id=request_id,
            future=future,
        )

        try:
            allow = await asyncio.wait_for(asyncio.shield(future), timeout=self._timeout)
        except asyncio.TimeoutError:
            log.info("Approval timed out for session %s request %s", session_id, request_id)
            self._pending.pop(conv_key, None)
            await self._signal.send(signal_conversation.identifier, _TIMEOUT_MESSAGE)
            allow = False
        finally:
            self._pending.pop(conv_key, None)

        await self._resolve(session_id, request_id, allow)
        return allow

    async def handle_reply(
        self,
        signal_conversation: ConversationKey,
        text: str,
    ) -> bool:
        """
        If there's a pending approval for this conversation, try to consume this
        reply as an answer. Returns True if the reply was consumed.
        """
        conv_key = signal_conversation.as_str()
        pending = self._pending.get(conv_key)
        if pending is None or pending.future.done():
            return False

        lower = text.strip().lower()
        if lower in ("y", "yes"):
            pending.future.set_result(True)
            return True
        elif lower in ("n", "no"):
            pending.future.set_result(False)
            return True
        else:
            await self._signal.send(signal_conversation.identifier, _WAITING_MESSAGE)
            return True  # consumed — don't forward to Goose

    async def handle_external_resolution(
        self,
        session_id: str,
        request_id: str,
        allow: bool,
    ) -> None:
        """
        Called when Desktop resolves a permission that was being awaited on Signal.
        Cancels the pending wait and notifies Signal.
        """
        for conv_key, pending in list(self._pending.items()):
            if pending.session_id == session_id and pending.request_id == request_id:
                if not pending.future.done():
                    pending.future.set_result(allow)
                conv = _conv_from_key(conv_key)
                await self._signal.send(conv, _DESKTOP_MESSAGE)
                self._pending.pop(conv_key, None)
                return

    async def _resolve(self, session_id: str, request_id: str, allow: bool) -> None:
        try:
            await self._acp.resolve_permission(session_id, request_id, allow)
        except NotImplementedError:
            log.debug(
                "resolve_permission not supported by goosed v1.30.0 — "
                "skipping ACP call (session=%s request=%s allow=%s)",
                session_id,
                request_id,
                allow,
            )


def _summarise_args(arguments: dict) -> str:
    if not arguments:
        return "(no arguments)"
    parts = []
    for k, v in list(arguments.items())[:3]:
        v_str = str(v)
        if len(v_str) > 60:
            v_str = v_str[:57] + "..."
        parts.append(f"{k}={v_str}")
    if len(arguments) > 3:
        parts.append(f"... +{len(arguments) - 3} more")
    return ", ".join(parts)


def _conv_from_key(conv_key: str) -> str:
    """Extract the identifier (phone number) from a ConversationKey string."""
    _, _, identifier = conv_key.partition(":")
    return identifier
