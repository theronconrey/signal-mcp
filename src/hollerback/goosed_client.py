"""
Client for the goosed agent REST API.

goosed runs HTTPS with a self-signed cert on a dynamic port.
Auth header: X-Secret-Key: <secret>
Port and secret are read from the goosed process environment at startup.
"""

import glob
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import httpx
import yaml


_GOOSE_CONFIG_PATH = Path.home() / ".config" / "goose" / "config.yaml"


def _read_goose_config_defaults(path: Path = _GOOSE_CONFIG_PATH) -> tuple[str | None, str | None]:
    """Return (provider, model) from Goose's own config.yaml, or (None, None)."""
    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None, None
    if not isinstance(raw, dict):
        return None, None
    return raw.get("GOOSE_PROVIDER"), raw.get("GOOSE_MODEL")


@dataclass
class GoosedConfig:
    port: int
    secret: str
    provider: str | None = None
    model: str | None = None

    @property
    def base_url(self) -> str:
        return f"https://127.0.0.1:{self.port}"


def discover_goosed() -> GoosedConfig:
    """
    Find a running goosed process and extract its port, secret, and
    GOOSE_PROVIDER / GOOSE_MODEL (when set). Reads /proc/<pid>/environ.
    Provider and model may be None — callers should treat them as optional.
    """
    for pid_dir in glob.glob("/proc/[0-9]*/exe"):
        try:
            pid = pid_dir.split("/")[2]
            exe = os.readlink(pid_dir)
            if os.path.basename(exe) != "goosed":
                continue

            if os.stat(f"/proc/{pid}").st_uid != os.getuid():
                continue

            env_path = f"/proc/{pid}/environ"
            with open(env_path, "rb") as f:
                env = dict(
                    item.split(b"=", 1)
                    for item in f.read().split(b"\x00")
                    if b"=" in item
                )

            secret_bytes = env.get(b"GOOSE_SERVER__SECRET_KEY")
            if not secret_bytes:
                continue
            secret = secret_bytes.decode()

            # Prefer GOOSE_PORT env var; fall back to socket scanning
            port_bytes = env.get(b"GOOSE_PORT")
            if port_bytes:
                port = int(port_bytes.decode())
            else:
                port = _find_listening_port(pid)
            if port is None:
                continue

            provider_bytes = env.get(b"GOOSE_PROVIDER")
            model_bytes = env.get(b"GOOSE_MODEL")
            provider = provider_bytes.decode() if provider_bytes else None
            model = model_bytes.decode() if model_bytes else None

            # Fall back to Goose's own config.yaml — Goose Desktop typically
            # reads that file and does NOT export the values to goosed's env.
            if provider is None or model is None:
                yaml_provider, yaml_model = _read_goose_config_defaults()
                provider = provider or yaml_provider
                model = model or yaml_model

            return GoosedConfig(port=port, secret=secret, provider=provider, model=model)

        except (OSError, PermissionError, ValueError):
            continue

    raise RuntimeError("goosed process not found or not accessible")


def _find_listening_port(pid: str) -> int | None:
    """
    Find the port goosed is listening on by:
    1. Getting socket inodes from /proc/<pid>/fd
    2. Matching those inodes against /proc/net/tcp6 (LISTEN state)
    """
    try:
        # Collect socket inodes open by this process
        socket_inodes: set[str] = set()
        fd_dir = f"/proc/{pid}/fd"
        for fd in os.listdir(fd_dir):
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
                if target.startswith("socket:["):
                    inode = target[8:-1]  # strip "socket:[" and "]"
                    socket_inodes.add(inode)
            except OSError:
                continue

        # Parse /proc/net/tcp6 for LISTEN sockets matching our inodes
        with open("/proc/net/tcp6") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 10:
                    continue
                state = parts[3]
                inode = parts[9]
                if state == "0A" and inode in socket_inodes:  # 0A = LISTEN
                    port_hex = parts[1].split(":")[1]
                    port = int(port_hex, 16)
                    if port > 1024:
                        return port

        # Also check /proc/net/tcp (IPv4)
        with open("/proc/net/tcp") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 10:
                    continue
                state = parts[3]
                inode = parts[9]
                if state == "0A" and inode in socket_inodes:
                    port_hex = parts[1].split(":")[1]
                    port = int(port_hex, 16)
                    if port > 1024:
                        return port
    except OSError:
        pass
    return None


class GoosedClient:
    def __init__(self, config: GoosedConfig):
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={"X-Secret-Key": config.secret},
            verify=False,
            timeout=httpx.Timeout(60.0, read=120.0),
        )

    async def status(self) -> bool:
        resp = await self._client.get("/status")
        return resp.status_code == 200 and resp.text.strip() == "ok"

    async def create_session(
        self,
        working_dir: str | None = None,
        provider: str = "mistral",
        model: str = "mistral-medium",
    ) -> str:
        """
        Create a new agent session and configure its provider. Returns session_id.

        New sessions start with provider_name=null and will return
        {"type":"Error","error":"Provider not set"} on /reply until configured.
        POST /agent/update_provider must be called after creation.
        """
        resp = await self._client.post(
            "/agent/start",
            json={"working_dir": working_dir or os.path.expanduser("~")},
        )
        resp.raise_for_status()
        session_id = resp.json()["id"]

        prov_resp = await self._client.post(
            "/agent/update_provider",
            json={"session_id": session_id, "provider": provider, "model": model},
        )
        prov_resp.raise_for_status()
        return session_id

    async def reply(self, session_id: str, text: str) -> AsyncIterator[dict]:
        """
        Send a message to a session. Yields SSE event dicts.
        Event types: Ping, Message, Finish.
        """
        payload = {
            "session_id": session_id,
            "user_message": {
                "role": "user",
                "created": int(time.time()),
                "metadata": {"userVisible": True, "agentVisible": True},
                "content": [{"type": "text", "text": text}],
            },
        }
        async with self._client.stream("POST", "/reply", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
