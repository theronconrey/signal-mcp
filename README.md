# goose-signal-gateway

A Python service that bridges Signal Messenger to [Goose](https://github.com/block/goose) via the `goosed` REST API. Send a message from your phone and get a full Goose agent reply — with tool use, streaming, and live edits — back in Signal.

> **Status:** Prototype. Core loop is fully implemented and tested. See [Known limitations](#known-limitations).

---

## Architecture

```
Signal (phone)
      │
      ▼
signal-cli daemon (HTTP, 127.0.0.1:8080)
      │  SSE event stream
      ▼
goose-signal-gateway          ← this repo
      │  REST + SSE (goosed API)
      ▼
goosed  ──────────────────────► Mistral / OpenAI / etc.
      │
      ▼
Goose Desktop (optional — shares the same goosed)
```

The gateway is a `goosed` API client. Sessions live in `goosed`. If you run Goose Desktop against the same `goosed` instance you can work in both interfaces simultaneously — though Desktop's session sidebar does not yet surface externally-created sessions (upstream issue filed; see [Desktop integration](docs/desktop-integration.md)).

---

## Prerequisites

**Linux only** — goosed discovery reads `/proc`. macOS/Windows not supported.

| Requirement | Version | Install |
|-------------|---------|---------|
| Fedora | 42+ | — |
| Java | 21+ | `sudo dnf install java-21-openjdk` |
| signal-cli | 0.13+ | `sudo dnf install signal-cli` |
| Python | 3.12+ | `sudo dnf install python3` |
| uv | any | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Goose Desktop | latest | [github.com/block/goose](https://github.com/block/goose) |

---

## Install

```bash
git clone https://github.com/theronconrey/goose-signal-gateway
cd goose-signal-gateway
uv tool install .
```

This puts `goose-signal` on your PATH.

---

## Setup

Run the interactive wizard:

```bash
goose-signal setup
```

The wizard will:

1. Check prerequisites (`signal-cli`, `java`).
2. Ask for your Signal bot phone number (E.164, e.g. `+16125551234`).
3. Ask for access policy (`pairing` is the default — unknown senders get a code).
4. Ask for a home conversation (where startup notifications go; default: your own number).
5. Write `~/.config/goose-signal-gateway/config.yaml`.
6. Test the signal-cli daemon connection.

**Linking signal-cli to a Signal account** (do this before `setup` if not already done):

```bash
signal-cli link -n "goose-signal-gateway"
```

This prints a `tsdevice://` URI. Render it as a QR code:

```bash
signal-cli link -n "goose-signal-gateway" | qrencode -t ansiutf8
```

On your phone: **Signal → Settings → Linked devices → Link new device** → scan.

---

## Running

### Foreground

```bash
goose-signal start
```

### Background (systemd user unit)

```bash
goose-signal start --detach   # installs unit, enables, starts
goose-signal status           # active / inactive
goose-signal stop
goose-signal logs -f          # follow via journald
```

### Verify health

```bash
goose-signal doctor
```

Runs 14 checks and reports ✓ / ✗ with specific remediation hints for each failure.

---

## Running with Goose Desktop

Both the gateway and Goose Desktop talk to the same `goosed` process. The gateway discovers it automatically by scanning `/proc` for the process and reading its port and auth secret — no manual configuration needed.

1. Start Goose Desktop normally.
2. Start the gateway: `goose-signal start`
3. Send a Signal message to the bot.
4. The gateway creates a `goosed` session for that conversation.

**Current limitation:** Goose Desktop's session sidebar does not poll `GET /sessions` — it reads local state only. Gateway-created sessions are fully functional in `goosed` but won't appear in the sidebar until an upstream Desktop fix lands. See [docs/desktop-integration.md](docs/desktop-integration.md) for details and a manual verification procedure.

---

## Running headless (server / VPS)

> Note: `acp.manage_goosed: true` (gateway spawns its own `goosed`) is not yet implemented. For now, start `goosed` manually before the gateway.

```bash
# Start goosed (requires Goose installed)
goosed &

# Start gateway
goose-signal start
```

Add both to systemd user units for unattended operation. A reference unit for `signal-cli` is at `systemd/signal-cli.service`.

---

## Pairing

The default access policy (`pairing`) requires unknown senders to be approved before the bot will respond.

When an unknown sender messages the bot they receive:

```
⚠️  This bot requires pairing. Ask the operator to run:

    goose-signal pairing approve ABCD23

Your pairing code expires in 60 minutes.
```

Operator commands:

```bash
goose-signal pairing list                  # show pending codes
goose-signal pairing approve ABCD23        # approve a sender
goose-signal pairing deny ABCD23           # reject a code
goose-signal pairing revoke +16125551234   # remove an approved sender
```

---

## Troubleshooting

Run `goose-signal doctor` first — it identifies and describes every fixable problem. Common issues:

**signal-cli daemon not running**
```bash
signal-cli --account +1XXXXXXXXXX daemon --http 127.0.0.1:8080
# or via systemd:
systemctl --user enable --now signal-cli@+1XXXXXXXXXX
```
(Install the template unit from `systemd/signal-cli.service`.)

**goosed not found**
The gateway discovers `goosed` via `/proc`. Start Goose Desktop or run `goosed` manually before starting the gateway.

**`Provider not set` errors in logs**
`goosed` v1.30.0 requires a provider to be configured via `POST /agent/update_provider` after session creation. The gateway does this automatically. If you see this error, the provider call may have failed — check that Goose Desktop has a valid provider configured.

**Edit-message failures**
Signal rate-limits message edits. On very long responses (multi-minute streaming) you may see edit errors in the logs. The final reply is always sent; intermediate live-edit updates may be dropped.

**Pairing code never arrives**
Confirm signal-cli is running and the bot account is linked. Check `goose-signal logs` for send errors.

---

## Security posture

- **Pairing is the default for a reason.** `open` DM policy gives anyone with your bot number shell-level access via Goose tool use. Only use `open` with `--i-accept-the-risk` if the bot number is private.
- **Tool approval routes to Signal.** When Goose wants to run a shell command, the gateway sends a yes/no prompt to Signal. You must reply before it proceeds.
- **The gateway has shell access.** Treat `~/.config/goose-signal-gateway/config.yaml` and the systemd unit like root credentials.
- **signal-cli key material** is at `~/.local/share/signal-cli` — mode `0700`. Do not expose it.
- **goosed auth token** is read from `/proc` at startup and stored in memory only. The pairing store and session map are written `0600`.

---

## Known limitations

- **Linux only** — goosed discovery reads `/proc`; macOS/Windows not supported.
- **Desktop session sidebar** — gateway sessions don't appear in Goose Desktop's sidebar (upstream issue filed against `block/goose`).
- **No session/load** — goosed v1.30.0 has no history-replay endpoint; restarting the gateway starts a fresh session for existing conversations.
- **No resolve_permission via ACP** — goosed v1.30.0 doesn't expose a permission-resolution endpoint; the approval flow sends the Signal prompt but the ACP handshake cannot complete.
- **signal-cli version skew** — the gateway is tested against signal-cli 0.13+. Older versions may not support `editMessage` or `sendTyping`.
- **Edit-message rate limits** — Signal can reject rapid edits on long responses.
- **No Desktop → Signal forwarding** — messages typed in Goose Desktop on a gateway session don't echo back to Signal (Phase 7c; deferred).
- **One session per sender** — no concept of threads or topics within a DM conversation.
- **No voice, video, stories, or reactions** — text only.

---

## Project structure

```
src/goose_signal_gateway/
├── acp_client.py      # goosed REST/SSE client
├── approvals.py       # Signal-side tool-approval flow
├── cli.py             # goose-signal CLI entry point
├── config.py          # config model + YAML load/save
├── dedup.py           # message deduplication
├── gateway.py         # main loop: receive → session → stream → edit
├── goosed_client.py   # goosed process discovery (/proc)
├── pairing.py         # sender pairing handshake
├── session_map.py     # persistent Signal conversation → session_id map
└── signal_client.py   # signal-cli HTTP client (send, edit, typing, SSE)

systemd/
├── goose-signal-gateway.service   # user unit for the gateway
└── signal-cli.service             # reference unit for signal-cli daemon

docs/
├── acp-findings.md        # goosed API contract (Phase 0.5)
├── desktop-integration.md # Desktop visibility investigation
└── PLAN.md                # original implementation plan
```

---

## License

MIT
