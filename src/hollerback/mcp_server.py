"""
Signal MCP server.

Exposes Signal send/list capabilities as MCP tools so any MCP-compatible
client (Claude CLI, Goose Desktop, etc.) can interact with Signal.

Auth: per-agent Bearer tokens. Each agent in config.yaml gets its own key.
Single agent configured = single mode. Multiple agents = party line (multi).

Claude CLI setup:
  claude mcp add signal-gateway http://<host>:<port>/mcp \
    --header "Authorization: Bearer <agent_key>"

Goose Desktop setup:
  Extensions → Add custom extension → HTTP
  Endpoint: http://<host>:<port>/mcp
  Request Headers: Authorization: Bearer <agent_key>
"""

import logging
import secrets as _secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import NotRequired, TypedDict

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from .signal_lint import detect_structural_markdown


_SEND_DESCRIPTION = """\
Send a Signal message to a paired contact.

Only contacts returned by list_signal_contacts are valid recipients — call
that tool first to get the correct phone number. Do not infer or guess
numbers from config, identity, or any other source.

Signal renders as plain text only. Do not use Markdown: no headings
(# ...), no bullet lists (- ...), no code fences (```), no link syntax
([text](url)). Compose as prose paragraphs. URLs go bare. Long messages
are fine when the content warrants it — Signal splits them gracefully —
but structure the thought as sentences, not as a formatted document.

Messages containing structural Markdown are rejected with an error;
rewrite as prose and resend.\
"""


class Identity(TypedDict):
    account: str
    mode: str
    goosed_connected: bool


class Contact(TypedDict):
    phone_number: str
    kind: str
    session_id: str


class Message(TypedDict):
    phone_number: str
    text: str
    timestamp: int


class SendResult(TypedDict):
    success: bool
    error: NotRequired[str]

log = logging.getLogger(__name__)


class MultiKeyTokenVerifier:
    """Validates a Bearer token against a list of named agent keys."""

    def __init__(self, agents: list[tuple[str, str]]):
        self._agents = agents

    async def verify_token(self, token: str) -> AccessToken | None:
        for name, key in self._agents:
            if _secrets.compare_digest(token, key):
                return AccessToken(token=token, client_id=name, scopes=[])
        return None


def build_mcp_server(
    signal_account: str,
    session_map,
    signal_client,
    message_buffer,
    agents: list[tuple[str, str]],
    host: str,
    port: int,
    goosed_connected: bool = False,
    style_prompt: str | None = None,
) -> FastMCP:
    """
    Build and return a configured FastMCP instance.

    signal_account:   the gateway's Signal phone number
    session_map:      SessionMap instance (live, already loaded)
    signal_client:    SignalClient instance (live)
    message_buffer:   MessageBuffer instance (live)
    agents:           list of (name, key) tuples; empty disables auth
    host:             host the server binds to (used for OAuth metadata URLs)
    port:             port to listen on
    goosed_connected: whether goosed is currently reachable
    style_prompt:     optional extra style guidance appended to the
                      send_signal_message tool description (always in context)
    """
    mode = "single" if len(agents) == 1 else "multi"

    @asynccontextmanager
    async def _lifespan(server) -> AsyncIterator[dict]:
        yield {}

    token_verifier = None
    auth_settings = None
    if agents:
        base_url = f"http://{host}:{port}"
        token_verifier = MultiKeyTokenVerifier(agents)
        auth_settings = AuthSettings(
            issuer_url=base_url,
            resource_server_url=base_url,
        )

    mcp = FastMCP(
        "hollerback",
        lifespan=_lifespan,
        port=port,
        auth=auth_settings,
        token_verifier=token_verifier,
    )

    @mcp.tool()
    async def get_signal_identity() -> Identity:
        """
        Return identity and mode information for this gateway.

        NOTE: 'account' is the gateway's own Signal number — not the owner's.
        To find the owner or other paired humans, call list_signal_contacts.
        """
        return {
            "account": signal_account,
            "mode": mode,
            "goosed_connected": goosed_connected,
        }

    @mcp.tool()
    async def list_signal_contacts() -> list[Contact]:
        """
        List humans who have paired with this gateway and can receive messages.

        These are the only valid recipients for send_signal_message. Call this
        first to discover phone numbers — do not guess or infer them from other
        sources. The gateway owner will appear here after pairing.
        """
        from .session_map import ConversationKey
        raw = await session_map.all()
        return [
            {
                "phone_number": ConversationKey.from_str(k).identifier,
                "kind": ConversationKey.from_str(k).kind,
                "session_id": v,
            }
            for k, v in raw.items()
        ]

    @mcp.tool()
    async def get_messages(phone_number: str | None = None, since: int = 0) -> list[Message]:
        """
        Retrieve messages received from Signal contacts.

        phone_number: optional filter — only return messages from this number.
        since: optional Unix timestamp (ms) — only return messages newer than this.
        Returns list of {"phone_number": str, "text": str, "timestamp": int}.
        """
        return await message_buffer.get(phone_number=phone_number, since=since)

    send_description = _SEND_DESCRIPTION
    if style_prompt:
        send_description += "\n\nAdditional style guidance from this gateway owner:\n" + style_prompt

    @mcp.tool(description=send_description)
    async def send_signal_message(phone_number: str, message: str) -> SendResult:
        from .session_map import ConversationKey

        lint_error = detect_structural_markdown(message)
        if lint_error is not None:
            log.info("MCP send_signal_message rejected: %s", lint_error)
            return {"success": False, "error": lint_error}

        key = ConversationKey(kind="dm", identifier=phone_number)
        session_id = await session_map.get(key)
        if session_id is None:
            return {
                "success": False,
                "error": (
                    f"{phone_number} has not initiated a conversation through this gateway. "
                    "Use list_signal_contacts to see valid recipients."
                ),
            }
        try:
            await signal_client.send(phone_number, message)
            log.info("MCP → Signal %s: %r", phone_number, message[:80])
            return {"success": True}
        except Exception as e:
            log.error("MCP send_signal_message failed: %s", e)
            return {"success": False, "error": str(e)}

    return mcp
