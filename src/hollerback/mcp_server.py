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

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

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
    async def get_signal_identity() -> dict:
        """Return identity and mode information for this gateway."""
        return {
            "account": signal_account,
            "mode": mode,
            "goosed_connected": goosed_connected,
        }

    @mcp.tool()
    async def list_signal_contacts() -> list[dict]:
        """
        List Signal contacts who have initiated a conversation through this gateway.
        Only contacts with an active session are returned — these are the numbers
        that can be messaged via send_signal_message.
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
    async def get_messages(phone_number: str | None = None, since: int = 0) -> list[dict]:
        """
        Retrieve messages received from Signal contacts.

        phone_number: optional filter — only return messages from this number.
        since: optional Unix timestamp (ms) — only return messages newer than this.
        Returns list of {"phone_number": str, "text": str, "timestamp": int}.
        """
        return await message_buffer.get(phone_number=phone_number, since=since)

    @mcp.tool()
    async def send_signal_message(phone_number: str, message: str) -> dict:
        """
        Send a Signal message to a contact.

        The contact must have previously initiated a conversation through this
        gateway (i.e. they appear in list_signal_contacts). Messages cannot be
        sent to unknown numbers.
        """
        from .session_map import ConversationKey
        key = ConversationKey(kind="dm", identifier=phone_number)
        session_id = await session_map.get(key)
        if session_id is None:
            return {
                "success": False,
                "error": f"{phone_number} has not initiated a conversation through this gateway",
            }
        try:
            await signal_client.send(phone_number, message)
            log.info("MCP → Signal %s: %r", phone_number, message[:80])
            return {"success": True}
        except Exception as e:
            log.error("MCP send_signal_message failed: %s", e)
            return {"success": False, "error": str(e)}

    return mcp
