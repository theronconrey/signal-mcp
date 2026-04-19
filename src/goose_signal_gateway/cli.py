"""
goose-signal CLI entry point.
"""

import asyncio
import importlib.metadata
import logging
import shutil
import signal
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .config import DEFAULT_CONFIG_PATH, Config, load_config, save_config

console = Console()
err_console = Console(stderr=True)


def _load_or_exit(path: Path) -> Config:
    try:
        return load_config(path)
    except FileNotFoundError:
        err_console.print(f"[red]Config not found:[/red] {path}")
        err_console.print("Run [bold]goose-signal setup[/bold] first.")
        sys.exit(1)
    except Exception as e:
        err_console.print(f"[red]Config error:[/red] {e}")
        sys.exit(1)


# ── root ──────────────────────────────────────────────────────────────────────

@click.group()
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG_PATH),
              show_default=True, help="Path to config file.")
@click.pass_context
def cli(ctx, config_path):
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = Path(config_path)


# ── version ───────────────────────────────────────────────────────────────────

@cli.command()
def version():
    """Print version and exit."""
    try:
        v = importlib.metadata.version("goose-signal-gateway")
    except importlib.metadata.PackageNotFoundError:
        v = "dev"
    console.print(v)


# ── start ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--account", default=None, help="Signal account (overrides config).")
@click.option("--detach", is_flag=True, help="Install and start systemd user unit.")
@click.option("--log-level", default=None,
              type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]))
@click.pass_context
def start(ctx, account, detach, log_level):
    """Run the gateway (foreground) or install as a systemd unit."""
    if detach:
        _start_detached()
        return

    config_path: Path = ctx.obj["config_path"]
    cfg = _load_or_exit(config_path)

    acct = account or cfg.daemon.account
    if not acct:
        err_console.print("[red]No Signal account configured.[/red] "
                          "Pass --account or run setup.")
        sys.exit(1)

    level = log_level or cfg.logging.level
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from .gateway import Gateway

    gw = Gateway(
        signal_account=acct,
        session_map_path=cfg.sessions.storage,
        pairing_path=cfg.pairing.storage,
        pairing_enabled=(cfg.access.dm_policy == "pairing"),
        allowed_users=cfg.access.allowed_users or None,
        code_ttl_minutes=cfg.pairing.code_ttl_minutes,
        home_conversation=cfg.home_conversation,
        mcp_enabled=cfg.mcp.enabled,
        mcp_port=cfg.mcp.port,
        mcp_secret=cfg.mcp.secret,
    )

    loop = asyncio.new_event_loop()

    def _shutdown(*_):
        console.print("\nShutting down...")
        loop.create_task(gw.stop())
        loop.stop()

    loop.add_signal_handler(signal.SIGINT, _shutdown)
    loop.add_signal_handler(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(gw.start())
    finally:
        loop.close()


def _start_detached():
    binary = shutil.which("goose-signal") or sys.argv[0]
    unit_dst = Path.home() / ".config" / "systemd" / "user" / "goose-signal-gateway.service"
    unit_dst.parent.mkdir(parents=True, exist_ok=True)
    unit_dst.write_text(
        "[Unit]\n"
        "Description=Goose Signal Gateway\n"
        "Documentation=https://github.com/theronconrey/signal-mcp\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={binary} start\n"
        "Restart=on-failure\n"
        "RestartSec=5s\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        "NoNewPrivileges=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now",
                    "goose-signal-gateway"], check=True)
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "goose-signal-gateway"],
        capture_output=True, text=True
    )
    if result.stdout.strip() == "active":
        console.print("[green]✓[/green] goose-signal-gateway is active.")
    else:
        err_console.print("[red]Unit did not become active.[/red] "
                          "Run: journalctl --user -u goose-signal-gateway")
        sys.exit(1)


# ── stop ──────────────────────────────────────────────────────────────────────

@cli.command()
def stop():
    """Stop the systemd user unit."""
    result = subprocess.run(
        ["systemctl", "--user", "stop", "goose-signal-gateway"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        console.print("[green]✓[/green] Stopped.")
    else:
        err_console.print(f"[red]Failed:[/red] {result.stderr.strip()}")
        sys.exit(1)


# ── status ────────────────────────────────────────────────────────────────────

@cli.command()
def status():
    """Show gateway running status."""
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "goose-signal-gateway"],
        capture_output=True, text=True
    )
    state = result.stdout.strip()
    color = "green" if state == "active" else "red"
    console.print(f"[{color}]{state}[/{color}]")


# ── logs ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("-f", "--follow", is_flag=True, help="Follow output.")
def logs(follow):
    """Tail gateway logs via journald."""
    cmd = ["journalctl", "--user", "-u", "goose-signal-gateway", "--no-pager"]
    if follow:
        cmd.append("-f")
    subprocess.run(cmd)


# ── doctor ────────────────────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def doctor(ctx):
    """Run health checks on every component."""
    config_path: Path = ctx.obj["config_path"]
    checks = _run_doctor(config_path)
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)

    table = Table(show_header=False, box=None, padding=(0, 1))
    for label, ok, detail in checks:
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        row = f"{label}"
        if detail:
            row += f"  [dim]{detail}[/dim]"
        table.add_row(icon, row)

    console.print(table)
    color = "green" if passed == total else "yellow" if passed > total // 2 else "red"
    console.print(f"\n[{color}]{passed}/{total} checks passed.[/{color}]")
    if passed < total:
        sys.exit(1)


def _run_doctor(config_path: Path) -> list[tuple[str, bool, str]]:
    import httpx

    checks: list[tuple[str, bool, str]] = []

    def check(label, ok, detail=""):
        checks.append((label, ok, detail))

    # 1. Config
    cfg = None
    try:
        cfg = load_config(config_path)
        check("Config file exists and parses", True, str(config_path))
    except FileNotFoundError:
        check("Config file exists and parses", False,
              f"not found — run: goose-signal setup")
        return checks
    except Exception as e:
        check("Config file exists and parses", False, str(e))
        return checks

    # 2. signal-cli binary
    cli_bin = shutil.which("signal-cli")
    check("signal-cli binary found", bool(cli_bin),
          cli_bin or "sudo dnf install signal-cli")

    # 3. signal-cli daemon reachable
    try:
        resp = httpx.get("http://127.0.0.1:8080/api/v1/rpc", timeout=3)
        check("signal-cli daemon reachable", True)
    except Exception:
        check("signal-cli daemon reachable", False,
              "signal-cli --account <number> daemon --http 127.0.0.1:8080\n"
              "  or: systemctl --user enable --now signal-cli@+1XXXXXXXXXX")

    # 4. Java 21+
    java = shutil.which("java")
    if java:
        try:
            out = subprocess.run(
                ["java", "-version"], capture_output=True, text=True
            ).stderr
            version_line = out.splitlines()[0] if out else ""
            maj = _java_major(version_line)
            ok = maj is not None and maj >= 21
            check("Java 21+", ok,
                  version_line if ok else f"found {version_line} — need 21+")
        except Exception as e:
            check("Java 21+", False, str(e))
    else:
        check("Java 21+", False, "sudo dnf install java-21-openjdk")

    # 5. Signal account configured
    acct = cfg.daemon.account
    check("Signal account configured", bool(acct),
          acct or "set daemon.account in config")

    # 6. SSE stream opens
    try:
        import httpx as _httpx
        with _httpx.Client(timeout=5) as c:
            with c.stream("GET", "http://127.0.0.1:8080/api/v1/events") as r:
                ok = r.status_code == 200
        check("Signal SSE stream opens", ok)
    except Exception:
        check("Signal SSE stream opens", False,
              "signal-cli daemon must be running")

    # 7. goosed binary (warning — not a hard fail)
    goosed_bin = shutil.which("goosed") or shutil.which("goose")
    check("goose/goosed binary found (optional)",
          bool(goosed_bin),
          goosed_bin or "not found — install Goose Desktop or goose CLI")

    # 8. goosed reachable
    try:
        from .goosed_client import discover_goosed
        gcfg = discover_goosed()
        check("goosed reachable", True, f"port {gcfg.port}")
        goosed_cfg = gcfg
    except Exception as e:
        check("goosed reachable", False,
              "start Goose Desktop or run goosed manually")
        goosed_cfg = None

    # 9. ACP initialize
    if goosed_cfg:
        try:
            from .acp_client import AcpClient

            async def _init():
                async with AcpClient(goosed_cfg) as c:
                    return await c.initialize()

            result = asyncio.run(_init())
            check("ACP initialize", result.healthy)
        except Exception as e:
            check("ACP initialize", False, str(e))
    else:
        check("ACP initialize", False, "skipped (goosed not reachable)")

    # 10. Test session create
    if goosed_cfg:
        try:
            from .acp_client import AcpClient

            async def _session():
                async with AcpClient(goosed_cfg) as c:
                    sid = await c.session_new(cwd=str(Path.home()))
                    return sid

            sid = asyncio.run(_session())
            check("Test session create", bool(sid), sid)
        except Exception as e:
            check("Test session create", False, str(e))
    else:
        check("Test session create", False, "skipped (goosed not reachable)")

    # 11. Metadata (goosed v1.30.0 doesn't support it — note limitation)
    check("Session metadata (display_name)",
          False,
          "goosed v1.30.0 lacks metadata field — upstream issue pending")

    # 12. systemd unit installed
    unit = Path.home() / ".config" / "systemd" / "user" / "goose-signal-gateway.service"
    check("systemd unit installed", unit.exists(),
          str(unit) if unit.exists() else "run: goose-signal start --detach")

    # 13. systemd unit active
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "goose-signal-gateway"],
        capture_output=True, text=True
    )
    active = result.stdout.strip() == "active"
    check("systemd unit active", active,
          "" if active else "run: goose-signal start --detach")

    # 14. home_conversation in allowed_users
    hc = cfg.home_conversation
    if hc:
        in_list = (
            cfg.access.dm_policy == "open"
            or hc in cfg.access.allowed_users
        )
        check("home_conversation in allowed_users", in_list,
              "" if in_list else f"add {hc} to access.allowed_users")
    else:
        check("home_conversation configured", False,
              "set home_conversation in config")

    return checks


def _java_major(version_line: str) -> int | None:
    import re
    m = re.search(r'"(\d+)', version_line)
    if m:
        return int(m.group(1))
    return None


# ── sessions ──────────────────────────────────────────────────────────────────

@cli.command("sessions")
@click.pass_context
def sessions_cmd(ctx):
    """List ACP sessions created by the gateway."""
    config_path: Path = ctx.obj["config_path"]
    cfg = _load_or_exit(config_path)

    async def _list():
        from .session_map import SessionMap
        sm = await SessionMap.load(cfg.sessions.storage)
        return await sm.all()

    mapping = asyncio.run(_list())

    if not mapping:
        console.print("[dim]No sessions.[/dim]")
        return

    table = Table("Signal conversation", "ACP session_id")
    for conv_key, session_id in sorted(mapping.items()):
        table.add_row(conv_key, session_id)
    console.print(table)


# ── pairing ───────────────────────────────────────────────────────────────────

@cli.group()
@click.pass_context
def pairing(ctx):
    """Manage Signal pairing codes."""
    pass


def _pairing_store(cfg: Config):
    from .pairing import PairingStore
    return PairingStore(
        path=cfg.pairing.storage,
        code_ttl=timedelta(minutes=cfg.pairing.code_ttl_minutes),
        max_pending=cfg.pairing.max_pending,
        allowed_users=cfg.access.allowed_users,
    )


@pairing.command("list")
@click.pass_context
def pairing_list(ctx):
    """List pending pairing codes."""
    cfg = _load_or_exit(ctx.obj["config_path"])
    store = _pairing_store(cfg)
    pending = store.list_pending()
    if not pending:
        console.print("[dim]No pending codes.[/dim]")
        return
    import datetime
    table = Table("Code", "Source", "Expires")
    for p in pending:
        exp = datetime.datetime.fromtimestamp(p.expires_at).strftime("%H:%M:%S")
        table.add_row(p.code, p.source, exp)
    console.print(table)


@pairing.command("approve")
@click.argument("code")
@click.pass_context
def pairing_approve(ctx, code):
    """Approve a pairing code."""
    cfg = _load_or_exit(ctx.obj["config_path"])
    store = _pairing_store(cfg)
    source = store.approve(code.upper())
    if source:
        console.print(f"[green]✓[/green] Approved {source}")
    else:
        err_console.print(f"[red]Code not found or expired:[/red] {code}")
        sys.exit(1)


@pairing.command("deny")
@click.argument("code")
@click.pass_context
def pairing_deny(ctx, code):
    """Deny a pairing code."""
    cfg = _load_or_exit(ctx.obj["config_path"])
    store = _pairing_store(cfg)
    if store.deny(code.upper()):
        console.print(f"[green]✓[/green] Denied {code.upper()}")
    else:
        err_console.print(f"[red]Code not found:[/red] {code}")
        sys.exit(1)


@pairing.command("revoke")
@click.argument("source")
@click.pass_context
def pairing_revoke(ctx, source):
    """Revoke an approved sender."""
    cfg = _load_or_exit(ctx.obj["config_path"])
    store = _pairing_store(cfg)
    if store.revoke_approval(source):
        console.print(f"[green]✓[/green] Revoked {source}")
    else:
        err_console.print(f"[red]Not found in approved list:[/red] {source}")
        sys.exit(1)


# ── setup ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def setup(ctx):
    """Interactive first-run setup wizard."""
    config_path: Path = ctx.obj["config_path"]
    console.rule("[bold]goose-signal-gateway setup[/bold]")

    # Prerequisites
    console.print("\n[bold]Checking prerequisites...[/bold]")
    missing = []

    if not shutil.which("signal-cli"):
        err_console.print("[red]✗[/red] signal-cli not found.\n"
                          "  Install: sudo dnf install signal-cli\n"
                          "  Or: https://github.com/AsamK/signal-cli#installation")
        missing.append("signal-cli")
    else:
        console.print("[green]✓[/green] signal-cli found")

    if not shutil.which("java"):
        err_console.print("[red]✗[/red] java not found.\n"
                          "  Install: sudo dnf install java-21-openjdk")
        missing.append("java")
    else:
        console.print("[green]✓[/green] java found")

    goose_bin = shutil.which("goosed") or shutil.which("goose")
    if not goose_bin:
        console.print("[yellow]⚠[/yellow] goose/goosed not found — "
                      "install Goose Desktop or goose CLI for full functionality")
    else:
        console.print(f"[green]✓[/green] goose found: {goose_bin}")

    if missing:
        err_console.print(f"\n[red]Missing required tools: {', '.join(missing)}[/red]")
        sys.exit(1)

    # Signal account
    console.print("\n[bold]Signal account[/bold]")
    account = click.prompt("Signal phone number (E.164, e.g. +16125551234)")

    # Access control
    console.print("\n[bold]Access control[/bold]")
    policy = click.prompt(
        "DM policy",
        type=click.Choice(["pairing", "allowlist", "open"]),
        default="pairing",
    )
    allowed: list[str] = []
    if policy == "allowlist":
        raw = click.prompt("Allowed numbers (comma-separated E.164)")
        allowed = [n.strip() for n in raw.split(",") if n.strip()]

    # Home conversation
    console.print("\n[bold]Notifications[/bold]")
    home = click.prompt(
        "Home conversation (your number, for startup notifications)",
        default=account,
    )
    if home and policy == "allowlist" and home not in allowed:
        allowed.append(home)

    # Build and save config
    import secrets
    cfg = Config()
    cfg.daemon.account = account
    cfg.access.dm_policy = policy
    cfg.access.allowed_users = allowed
    cfg.home_conversation = home
    cfg.mcp.secret = secrets.token_urlsafe(32)

    if config_path.exists():
        if not click.confirm(f"\nOverwrite existing config at {config_path}?"):
            console.print("Aborted.")
            sys.exit(0)

    save_config(cfg, config_path)
    console.print(f"\n[green]✓[/green] Config written to {config_path}")

    # Test signal-cli daemon
    console.print("\n[bold]Testing connections...[/bold]")
    try:
        import httpx
        httpx.get("http://127.0.0.1:8080/api/v1/rpc", timeout=3)
        console.print("[green]✓[/green] signal-cli daemon reachable")
    except Exception:
        console.print("[yellow]⚠[/yellow] signal-cli daemon not running — "
                      "start it before running goose-signal start")

    console.print("\n[bold]Setup complete.[/bold]")
    console.print(f"\n[bold]MCP secret[/bold] (use as X-Gateway-Key header in your MCP client):")
    console.print(f"    [yellow]{cfg.mcp.secret}[/yellow]")
    console.print("\nStart the gateway:")
    console.print("    goose-signal start               [dim](foreground)[/dim]")
    console.print("    goose-signal start --detach      [dim](systemd user unit)[/dim]")
    console.print("\nVerify health:")
    console.print("    goose-signal doctor")


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
