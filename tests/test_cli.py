import asyncio
import pytest
from pathlib import Path
from click.testing import CliRunner

from hollerback.cli import cli
from hollerback.config import Config, save_config
from hollerback.pairing import PairingStore


def make_runner():
    return CliRunner()


def write_config(tmp_path) -> Path:
    cfg_path = tmp_path / "config.yaml"
    cfg = Config()
    cfg.daemon.account = "+10000000000"
    cfg.access.dm_policy = "pairing"
    cfg.home_conversation = "+10000000000"
    cfg.access.allowed_users = ["+10000000000"]
    cfg.pairing.storage = tmp_path / "pairing.json"
    cfg.sessions.storage = tmp_path / "sessions.json"
    save_config(cfg, cfg_path)
    return cfg_path


# ── version ───────────────────────────────────────────────────────────────────

def test_version():
    runner = make_runner()
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert result.output.strip()  # something printed


# ── doctor: missing config ────────────────────────────────────────────────────

def test_doctor_missing_config(tmp_path):
    runner = make_runner()
    result = runner.invoke(cli, [
        "--config", str(tmp_path / "nonexistent.yaml"),
        "doctor",
    ])
    assert result.exit_code != 0
    assert "0/1" in result.output or "not found" in result.output.lower() or "0/" in result.output


# ── doctor: good config (network checks will fail, but config check passes) ───

def test_doctor_config_check_passes(tmp_path):
    cfg_path = write_config(tmp_path)
    runner = make_runner()
    result = runner.invoke(cli, ["--config", str(cfg_path), "doctor"])
    # Config check should show ✓ even if network checks fail
    assert "✓" in result.output


# ── sessions ──────────────────────────────────────────────────────────────────

def test_sessions_empty(tmp_path):
    cfg_path = write_config(tmp_path)
    runner = make_runner()
    result = runner.invoke(cli, ["--config", str(cfg_path), "sessions"])
    assert result.exit_code == 0
    assert "No sessions" in result.output


def test_sessions_lists_map(tmp_path):
    import json
    cfg_path = write_config(tmp_path)
    sessions_path = tmp_path / "sessions.json"
    # Write directly — avoids async inside sync test
    sessions_path.write_text(json.dumps({"dm:+1111": "session_abc"}))
    sessions_path.chmod(0o600)

    runner = make_runner()
    result = runner.invoke(cli, ["--config", str(cfg_path), "sessions"])
    assert result.exit_code == 0
    assert "+1111" in result.output
    assert "session_abc" in result.output


# ── pairing ───────────────────────────────────────────────────────────────────

def test_pairing_list_empty(tmp_path):
    cfg_path = write_config(tmp_path)
    runner = make_runner()
    result = runner.invoke(cli, ["--config", str(cfg_path), "pairing", "list"])
    assert result.exit_code == 0
    assert "No pending" in result.output


def test_pairing_approve_deny_cycle(tmp_path):
    cfg_path = write_config(tmp_path)

    from hollerback.config import load_config
    cfg = load_config(cfg_path)
    store = PairingStore(cfg.pairing.storage)
    code = store.request_code("+9999")

    runner = make_runner()

    # approve
    result = runner.invoke(cli, ["--config", str(cfg_path), "pairing", "approve", code])
    assert result.exit_code == 0
    assert "+9999" in result.output

    # revoke
    result = runner.invoke(cli, ["--config", str(cfg_path), "pairing", "revoke", "+9999"])
    assert result.exit_code == 0

    # deny nonexistent
    result = runner.invoke(cli, ["--config", str(cfg_path), "pairing", "deny", "XXXXXX"])
    assert result.exit_code != 0


def test_pairing_approve_expired(tmp_path):
    import time
    from datetime import timedelta
    cfg_path = write_config(tmp_path)

    from hollerback.config import load_config
    cfg = load_config(cfg_path)
    store = PairingStore(cfg.pairing.storage, code_ttl=timedelta(seconds=1))
    code = store.request_code("+8888")
    # manually expire
    p = store._pending[code]
    store._pending[code] = p.__class__(
        code=p.code, source=p.source, issued_at=p.issued_at, expires_at=time.time() - 1
    )
    store._flush()  # persist the expired timestamp so the CLI sees it

    runner = make_runner()
    result = runner.invoke(cli, ["--config", str(cfg_path), "pairing", "approve", code])
    assert result.exit_code != 0
