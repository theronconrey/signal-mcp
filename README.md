# signal-mcp

A self-hosted MCP server that gives any AI agent bidirectional access to Signal Messenger. Send messages from your phone and get a full agent reply — or let the agent reach back out to you proactively.

Built around [Goose](https://github.com/block/goose) and [signal-cli](https://github.com/AsamK/signal-cli).

> **Status:** Prototype. Core loop is fully implemented and smoke-tested. See [Known limitations](#known-limitations).

---

## What it does

**Inbound (Signal → Agent)**
A message arrives on Signal → the gateway picks it up → creates or resumes a Goose session → streams the reply back to Signal. Read receipts (filled double-ticks) fire immediately on receipt. Typing indicators show while Goose is thinking.

**Outbound (Agent → Signal)**
Any MCP-compatible client (Goose Desktop, Claude Desktop, Cursor, etc.) can connect to the gateway's MCP endpoint and send Signal messages, list known contacts, or query the gateway identity — directly from a chat session.

---

## Architecture

```
Signal (phone)
      │
      ▼
signal-cli daemon (HTTP, 127.0.0.1:8080)
      │  SSE event stream
      ▼
signal-mcp                        ← this repo
      │  ├─ REST + SSE (goosed API)        inbound path
      │  └─ MCP server (port 7322)         outbound path
      ▼
goosed  ──────────────────────► Mistral / OpenAI / etc.
```

---

## MCP Extension

Once the gateway is running, register it in any MCP-compatible client:

| Field | Value |
|-------|-------|
| Type | HTTP |
| Endpoint | `http://127.0.0.1:7322/mcp` |
| Header name | `X-Gateway-Key` |
| Header value | *(your `gateway_secret` from config.yaml)* |

### Available tools

| Tool | Description |
|------|-------------|
| `get_signal_identity` | Returns the Signal account number the gateway is running as |
| `list_signal_contacts` | Lists contacts with active sessions (numbers Goose can message) |
| `send_signal_message(phone_number, message)` | Sends a Signal message to a known contact |

**Contact gating:** a phone number must initiate a conversation through the gateway (passing the pairing flow) before the agent can message them. The agent cannot cold-call arbitrary numbers.

---

## Prerequisites

**Linux only** — goosed discovery reads `/proc`. macOS/Windows not supported.

| Requirement | Version | Install |
|-------------|---------|---------|
| Java | 21+ | `sudo dnf install java-21-openjdk` |
| signal-cli | 0.13+ | `sudo dnf install signal-cli` |
| Python | 3.12+ | managed by `uv` |
| uv | any | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Goose Desktop | latest | [github.com/block/goose](https://github.com/block/goose) |

---

## Install

```bash
git clone https://github.com/theronconrey/signal-mcp
cd signal-mcp
uv sync
```

---

## Setup

**1. Link signal-cli to a Signal account** (if not already done):

```bash
signal-cli link -n "signal-mcp" | qrencode -t ansiutf8
```

On your phone: **Signal → Settings → Linked devices → Link new device** → scan.

**2. Run the setup wizard:**

```bash
uv run goose-signal setup
```

The wizard will:
1. Check prerequisites (`signal-cli`, `java`, `goosed`)
2. Ask for your Signal bot phone number (E.164, e.g. `+16125551234`)
3. Ask for access policy (`pairing` is the default)
4. Ask for a home conversation (where startup/shutdown notifications go)
5. Generate a `gateway_secret` for MCP auth
6. Write `~/.config/goose-signal-gateway/config.yaml`

---

## Running

### As a systemd user service (recommended)

```bash
# Install and enable
cp systemd/goose-signal-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now goose-signal-gateway

# Check status
systemctl --user status goose-signal-gateway

# Follow logs
journalctl --user -u goose-signal-gateway -f
```

### Foreground

```bash
uv run goose-signal start
```

### Health check

```bash
uv run goose-signal doctor
```

---

## Pairing

The default access policy (`pairing`) requires unknown senders to be approved before the bot will respond.

When an unknown number messages the bot they receive a pairing code. The operator approves it:

```bash
uv run goose-signal pairing list                  # show pending codes
uv run goose-signal pairing approve ABCD23        # approve
uv run goose-signal pairing deny ABCD23           # reject
uv run goose-signal pairing revoke +16125551234   # remove approved sender
```

Once approved, the sender can converse with Goose — and Goose can message them back via the MCP `send_signal_message` tool.

---

## Security posture

- **Pairing is the default for a reason.** `open` DM policy gives anyone with your bot number shell-level access via Goose tool use.
- **Tool approval routes to Signal.** When Goose wants to run a shell command, the gateway sends a yes/no prompt to Signal. You must reply before it proceeds.
- **MCP auth is a shared secret.** The `gateway_secret` in `config.yaml` grants full Signal send access. Treat it like a password.
- **The gateway has shell access.** Treat `~/.config/goose-signal-gateway/config.yaml` like root credentials.
- **signal-cli key material** is at `~/.local/share/signal-cli` — mode `0700`. Do not expose it.

---

## Known limitations

- **Linux only** — goosed discovery reads `/proc`; macOS/Windows not supported.
- **Desktop session sidebar** — gateway sessions appear in Goose Desktop's sidebar only after a Desktop restart (`loadSessions()` runs at startup; there is no push notification for externally-created sessions). Upstream fix needed: a `sessionCreated` WebSocket event from goosed.
- **Desktop real-time message updates** — when the gateway injects a message into an already-open session via `POST /reply`, the Desktop UI does not refresh to show the new exchange. goosed does not broadcast session writes to existing UI subscribers. Workaround: close and reopen the session in Desktop to reload the full history. Upstream fix needed: a `sessionUpdated` WebSocket event (or shared SSE broadcast) from goosed.
- **goosed port changes on restart** — goosed binds to a random port each time Goose Desktop starts. The gateway auto-reconnects on the next inbound Signal message, but any messages received while goosed is down are lost.
- **No session history replay** — goosed v1.30.0 has no history endpoint; restarting the gateway starts fresh sessions.
- **No `resolve_permission` via ACP** — the tool-approval flow sends a Signal prompt but the ACP handshake cannot complete (goosed v1.30.0 limitation).
- **One session per sender** — no threads or topics within a DM conversation.
- **Text only** — no voice, video, reactions, or attachments.
- **signal-cli 0.14.2 quirks** — `editMessage` and `sendReadReceipt` not implemented in the HTTP daemon; workarounds are in place (see `docs/`).

---

## Project structure

```
src/goose_signal_gateway/
├── acp_client.py      # goosed REST/SSE client
├── approvals.py       # Signal-side tool-approval flow
├── cli.py             # goose-signal CLI entry point
├── config.py          # config model + YAML load/save
├── dedup.py           # message deduplication
├── gateway.py         # main loop: receive → session → stream → reply
├── goosed_client.py   # goosed process discovery (/proc)
├── mcp_server.py      # MCP HTTP server (signal tools)
├── pairing.py         # sender pairing handshake
├── session_map.py     # persistent Signal conversation → session_id map
└── signal_client.py   # signal-cli HTTP client (send, typing, receipts, SSE)

systemd/
├── goose-signal-gateway.service   # user unit for the gateway
└── signal-cli.service             # reference unit for signal-cli daemon

docs/
├── acp-findings.md          # goosed API contract notes
├── desktop-integration.md   # Desktop session visibility investigation
└── mcp-server-research.md   # MCP direction research and Goose Desktop internals
```

---

## License

MIT
