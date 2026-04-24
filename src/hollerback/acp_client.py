"""
ACP client wrapping the goosed REST/SSE API.

goosed v1.30.0 deviations from the plan's ACP spec (see HANDOFF_REPORT.md):
  - No initialize handshake; initialize() calls GET /status instead.
  - POST /agent/start accepts only working_dir; metadata is silently dropped.
  - No session/load endpoint; session_load() raises NotImplementedError.
  - No resolve_permission endpoint; resolve_permission() raises NotImplementedError.
  - Permission requests are not surfaced via the REST API at all.
  - SSE event types are Ping/Message/Finish/Error, not standard ACP notifications.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Literal

import httpx

from .goosed_client import GoosedConfig, discover_goosed

log = logging.getLogger(__name__)


class AcpStreamInterruptedError(Exception):
    """Raised when the SSE stream drops unexpectedly mid-prompt."""


class AcpConnectError(AcpStreamInterruptedError):
    """Raised when the connection to goosed fails entirely (port changed, process restarted)."""


@dataclass(frozen=True)
class SessionSummary:
    id: str
    name: str
    working_dir: str


@dataclass(frozen=True)
class InitializeResult:
    server_url: str
    healthy: bool


@dataclass(frozen=True)
class SessionNotification:
    kind: Literal[
        "agent_message_chunk",
        "agent_thought_chunk",
        "user_message_chunk",
        "tool_call",
        "tool_result",
        "permission_request",
        "session_complete",
    ]
    session_id: str
    payload: dict


class AcpClient:
    def __init__(self, config: GoosedConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={"X-Secret-Key": config.secret},
            verify=False,
            timeout=httpx.Timeout(60.0, read=120.0),
        )

    @classmethod
    def from_discovery(cls) -> "AcpClient":
        """Discover a running goosed process and connect to it."""
        return cls(discover_goosed())

    async def __aenter__(self) -> "AcpClient":
        await self.initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def initialize(self) -> InitializeResult:
        """Health-check goosed. Raises RuntimeError if unreachable."""
        resp = await self._client.get("/status")
        healthy = resp.status_code == 200 and resp.text.strip() == "ok"
        if not healthy:
            raise RuntimeError(f"goosed health check failed: {resp.status_code} {resp.text!r}")
        return InitializeResult(server_url=self._config.base_url, healthy=True)

    async def health_check(self, timeout: float = 3.0) -> bool:
        """Soft health probe. Returns False instead of raising on any failure."""
        try:
            resp = await self._client.get("/status", timeout=timeout)
            return resp.status_code == 200 and resp.text.strip() == "ok"
        except Exception:
            return False

    @property
    def config(self) -> GoosedConfig:
        """Read-only access to the discovered goosed config (port, secret, provider, model)."""
        return self._config

    async def session_new(
        self,
        cwd: str,
        provider: str,
        model: str,
        mcp_servers: list[dict] | None = None,
        metadata: dict | None = None,
    ) -> str:
        """
        Create a new session and configure its provider. Returns session_id.

        NOTE: goosed /agent/start does not accept metadata; the metadata param
        is accepted for interface compatibility but is silently dropped.
        mcp_servers is likewise not supported by goosed's start endpoint.
        """
        if metadata:
            log.debug("session_new: metadata ignored (not supported by goosed v1.30.0): %s", metadata)

        resp = await self._client.post("/agent/start", json={"working_dir": cwd})
        resp.raise_for_status()
        session_id: str = resp.json()["id"]

        prov = await self._client.post(
            "/agent/update_provider",
            json={"session_id": session_id, "provider": provider, "model": model},
        )
        prov.raise_for_status()
        return session_id

    async def session_prompt(
        self,
        session_id: str,
        prompt: str,
    ) -> AsyncIterator[SessionNotification]:
        """
        Send a user message; stream assistant notifications until session_complete.

        Yields SessionNotification with kind:
          - agent_message_chunk  (Message SSE events)
          - session_complete     (Finish SSE event)

        Raises AcpStreamInterruptedError if the SSE stream drops before Finish.
        """
        payload = {
            "session_id": session_id,
            "user_message": {
                "role": "user",
                "created": int(time.time()),
                "metadata": {"userVisible": True, "agentVisible": True},
                "content": [{"type": "text", "text": prompt}],
            },
        }
        finished = False
        try:
            async with self._client.stream("POST", "/reply", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    kind = event.get("type")

                    if kind == "Ping":
                        continue
                    elif kind == "Message":
                        yield SessionNotification(
                            kind="agent_message_chunk",
                            session_id=session_id,
                            payload=event["message"],
                        )
                    elif kind == "Finish":
                        finished = True
                        yield SessionNotification(
                            kind="session_complete",
                            session_id=session_id,
                            payload=event,
                        )
                        break
                    elif kind == "Error":
                        raise RuntimeError(f"goosed error: {event.get('error')}")
        except httpx.ConnectError as exc:
            raise AcpConnectError(
                f"Cannot connect to goosed for session {session_id}"
            ) from exc
        except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
            raise AcpStreamInterruptedError(
                f"SSE stream dropped for session {session_id}"
            ) from exc

        if not finished:
            raise AcpStreamInterruptedError(
                f"SSE stream ended without Finish for session {session_id}"
            )

    async def session_load(self, session_id: str) -> AsyncIterator[SessionNotification]:
        """Not supported by goosed v1.30.0 — no session/load endpoint exists."""
        raise NotImplementedError(
            "goosed v1.30.0 has no session/load endpoint; history replay is unsupported"
        )
        # make the type checker happy
        yield  # type: ignore[misc]

    async def resolve_permission(
        self,
        session_id: str,
        request_id: str,
        allow: bool,
    ) -> None:
        """Not supported by goosed v1.30.0 — permission requests are not REST-accessible."""
        raise NotImplementedError(
            "goosed v1.30.0 does not surface permission requests via the REST API"
        )

    async def list_sessions(self) -> list[SessionSummary]:
        resp = await self._client.get("/sessions")
        resp.raise_for_status()
        return [
            SessionSummary(
                id=s["id"],
                name=s.get("name", ""),
                working_dir=s.get("working_dir", ""),
            )
            for s in resp.json().get("sessions", [])
        ]

    async def session_exists(self, session_id: str) -> bool:
        """Return True if the session is still live in goosed."""
        sessions = await self.list_sessions()
        return any(s.id == session_id for s in sessions)

    async def close(self) -> None:
        await self._client.aclose()
