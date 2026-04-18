"""
Gateway configuration model.

Config is stored as YAML at ~/.config/goose-signal-gateway/config.yaml.
Atomic writes. Missing keys fall back to defaults.
"""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "goose-signal-gateway" / "config.yaml"
_STATE = Path.home() / ".local" / "state" / "goose-signal-gateway"
_SHARE = Path.home() / ".local" / "share" / "goose-signal-gateway"


@dataclass
class DaemonConfig:
    account: str = ""


@dataclass
class AcpConfig:
    url: str | None = None           # None = auto-discover via /proc
    manage_goosed: bool = False


@dataclass
class AccessConfig:
    dm_policy: Literal["pairing", "allowlist", "open"] = "pairing"
    allowed_users: list[str] = field(default_factory=list)


@dataclass
class PairingConfig:
    storage: Path = field(default_factory=lambda: _STATE / "pairing.json")
    code_ttl_minutes: int = 60
    max_pending: int = 3


@dataclass
class SessionsConfig:
    storage: Path = field(default_factory=lambda: _SHARE / "sessions.json")


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Path = field(default_factory=lambda: _STATE / "gateway.log")


@dataclass
class StreamConfig:
    edit_interval_ms: int = 500
    edit_char_threshold: int = 80


@dataclass
class Config:
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    acp: AcpConfig = field(default_factory=AcpConfig)
    access: AccessConfig = field(default_factory=AccessConfig)
    pairing: PairingConfig = field(default_factory=PairingConfig)
    sessions: SessionsConfig = field(default_factory=SessionsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    home_conversation: str | None = None


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    def _path(val: str | None, default: Path) -> Path:
        return Path(os.path.expanduser(val)) if val else default

    d = raw.get("daemon", {})
    a = raw.get("acp", {})
    ac = raw.get("access", {})
    p = raw.get("pairing", {})
    s = raw.get("sessions", {})
    lo = raw.get("logging", {})
    st = raw.get("stream", {})

    return Config(
        daemon=DaemonConfig(account=d.get("account", "")),
        acp=AcpConfig(
            url=a.get("url"),
            manage_goosed=a.get("manage_goosed", False),
        ),
        access=AccessConfig(
            dm_policy=ac.get("dm_policy", "pairing"),
            allowed_users=ac.get("allowed_users", []),
        ),
        pairing=PairingConfig(
            storage=_path(p.get("storage"), _STATE / "pairing.json"),
            code_ttl_minutes=p.get("code_ttl_minutes", 60),
            max_pending=p.get("max_pending", 3),
        ),
        sessions=SessionsConfig(
            storage=_path(s.get("storage"), _SHARE / "sessions.json"),
        ),
        logging=LoggingConfig(
            level=lo.get("level", "INFO"),
            file=_path(lo.get("file"), _STATE / "gateway.log"),
        ),
        stream=StreamConfig(
            edit_interval_ms=st.get("edit_interval_ms", 500),
            edit_char_threshold=st.get("edit_char_threshold", 80),
        ),
        home_conversation=raw.get("home_conversation"),
    )


def save_config(config: Config, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {
        "daemon": {"account": config.daemon.account},
        "acp": {
            "url": config.acp.url,
            "manage_goosed": config.acp.manage_goosed,
        },
        "access": {
            "dm_policy": config.access.dm_policy,
            "allowed_users": config.access.allowed_users,
        },
        "pairing": {
            "storage": str(config.pairing.storage),
            "code_ttl_minutes": config.pairing.code_ttl_minutes,
            "max_pending": config.pairing.max_pending,
        },
        "sessions": {"storage": str(config.sessions.storage)},
        "logging": {
            "level": config.logging.level,
            "file": str(config.logging.file),
        },
        "stream": {
            "edit_interval_ms": config.stream.edit_interval_ms,
            "edit_char_threshold": config.stream.edit_char_threshold,
        },
        "home_conversation": config.home_conversation,
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config_")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(raw, f, default_flow_style=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
