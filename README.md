# hollerback

A Signal Messenger gateway for your local Goose agent.

We're Goose users. We're Signal users. We wanted the two to talk to each other. We didn't want to wait.

---

## What it is

A small daemon on your Linux box that connects your Signal number to a goosed instance you already run. Humans message the Signal number; Goose answers. Any MCP client - Claude CLI, Cursor, Goose Desktop - can also send Signal messages through the same gateway as a tool.

Built on [signal-cli](https://github.com/AsamK/signal-cli). Inbound replies route through [Goose](https://github.com/aaif-goose/goose) via its ACP interface.

> **Status:** Prototype. Core loop implemented, smoke-tested in production on Fedora. See [What doesn't work yet](#what-doesnt-work-yet).

---

## Relationship to `goose gateway`

Goose v1.26.0 shipped an experimental first-party `goose gateway telegram` for reaching goose from Telegram. The PR title - "Gateway to chat to goose via Telegram etc" - suggests more channels may come. We're watching that space with interest.

In the meantime, hollerback is what a Signal gateway looks like built from the outside against ACP. If Goose eventually grows a `goose gateway signal`, great - hollerback becomes a reference, not a competitor. If Goose decides to keep gateways in-tree and not expand beyond Telegram for a while, hollerback fills the gap for people who want Signal working today.

Either outcome is fine. We built this because we want to use it today.

---

## Why not OpenClaw or Hermes Agent?

[OpenClaw](https://openclaw.ai/) and [Hermes Agent](https://github.com/nousresearch/hermes-agent) are excellent standalone personal-assistant platforms with broad messaging-channel support. If you're picking a platform FOR a specific messaging integration, they're likely the right choice...... today.

We already run goose. Our sessions, extensions, models, and memory live in goosed. Our workflows exist in goose. We didn't want to adopt a second agent runtime just to get Signal. hollerback extends the Goose we already have without having to use Telegram today.

If you're primarily in the Goose ecosystem and want Signal, hollerback is for you.

---

## Two use cases, one phone

hollerback is one phone - a dedicated Signal number - with two use cases sharing one process and one contact list.

**Use case 1: A bot answers your Signal number.** Someone messages the number, Goose picks up and replies in real time. Unattended, session-aware across conversations. This is the `goose gateway`-shaped use case.

**Use case 2: You (or an agent you're talking to) use the number to message people.** Any MCP client - Claude CLI, Claude Desktop, Cursor, Goose Desktop - connects via standard HTTP+Bearer auth and gets tools to send Signal messages, list paired contacts, and read inbound traffic. Fully model-agnostic. Works today. have Goose delegating work to other agents? You can also have them message you direct when work is complete. 

One phone, two use cases. They share the pairing roster, the contact pool, and the inbound message buffer - because they're literally the same phone. You can run either use case alone or both together.

---

## Which clients work for which use case

| | Goose Desktop | Claude / Cursor / other MCP |
|--|:-:|:-:|
| **Use case 1 - bot answers the phone** | | |
| &nbsp;&nbsp;Unattended auto-reply to Signal messages | ✅ | ❌ |
| &nbsp;&nbsp;Sessions persist across messages | ✅ | N/A |
| &nbsp;&nbsp;Typing indicators, read receipts | ✅ | N/A |
| &nbsp;&nbsp;Tool approval from Signal | 🟡 | N/A |
| **Use case 2 - you use the phone** | | |
| &nbsp;&nbsp;Agent sends Signal messages | ✅ | ✅ |
| &nbsp;&nbsp;List paired contacts | ✅ | ✅ |
| &nbsp;&nbsp;Read inbound message buffer | ✅ | ✅ |
| &nbsp;&nbsp;Identify the gateway | ✅ | ✅ |

- ✅ works today
- 🟡 implemented, upstream convos needed.
- ❌ not applicable: these agents don't run as a local daemon hollerback can route incoming messages into

---

## Architecture

```
Signal (phone)
      │
      ▼
signal-cli daemon (HTTP, 127.0.0.1:8080)
      │  SSE event stream
      ▼
hollerback                          ← this repo
      │  ├─ ACP (REST+SSE) to goosed          use case 1: bot answers the phone
      │  └─ MCP server (port 7322)            use case 2: agent uses the phone
      │            │
      │            ├──► Goose Desktop  ─┐
      │            ├──► Claude CLI      ├─ same Signal capabilities, per-agent keys
      │            ├──► Claude Desktop  │
      │            └──► Cursor etc.    ─┘
      ▼
Signal (phone)   ◄── replies
```

One process, two surfaces, one state. Details in `docs/`.

---

## Prerequisites

**Linux only** - goosed discovery reads `/proc`. macOS/Windows not supported.

| Requirement | Version | Install |
|-------------|---------|---------|
| Java | 21+ | `sudo dnf install java-21-openjdk` |
| signal-cli | 0.13+ | `sudo dnf install signal-cli` |
| Python | 3.12+ | managed by `uv` |
| uv | any | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Goose | latest | [aaif-goose/goose](https://github.com/aaif-goose/goose) - required for use case 1; not needed for use case 2 alone |

---

## Install

```bash
git clone https://github.com/theronconrey/hollerback
cd hollerback
uv sync
```

Or install from PyPI:

```bash
uv tool install hollerback
```

---

## Setup

Complete these steps once, in order.

**1. Register a dedicated Signal number with signal-cli**

The gateway needs its own phone number - a SIM, second number, or VoIP number (Google Voice works). Separate from your personal Signal account.

```bash
signal-cli --account +1XXXXXXXXXX register
signal-cli --account +1XXXXXXXXXX verify <code>
```

**2. Start the signal-cli daemon**

```bash
signal-cli --account +1XXXXXXXXXX daemon --http 127.0.0.1:8080
```

Or install it as a user service:

```bash
systemctl --user enable --now signal-cli@+1XXXXXXXXXX
```

**3. Run the setup wizard**

```bash
uv run hollerback setup
```

The wizard checks prerequisites, asks for your bot number and access policy, writes `~/.config/hollerback/config.yaml`, and writes your first agent key to `~/.config/hollerback/agent-keys/default.key` (mode 0600). It prints ready-to-paste connection commands for Claude CLI and Goose Desktop.

**4. Start the gateway** (start Goose Desktop first if you want use case 1):

```bash
uv run hollerback start --detach
```

**5. Verify**

```bash
uv run hollerback doctor
```

All green means you're ready. Send a message to your bot from Signal to test.

---

## Running

```bash
hollerback status             # is it active?
hollerback logs -f            # tail journald
hollerback stop
hollerback start              # foreground, for debugging
```

---

## Pairing

The default DM policy (`pairing`) requires unknown senders to be approved. Unknown numbers get a one-time code; the operator approves via CLI.

```bash
hollerback pairing list                      # pending codes
hollerback pairing approve ABCD23            # approve
hollerback pairing deny ABCD23               # reject
hollerback pairing revoke +16125551234       # remove approved sender
```

Codes are 6 characters from an unambiguous alphabet (no 0/O/1/I/L/-/_), TTL 60 minutes. Once approved, the sender can converse with the agent and the agent can message them back via MCP.

---

## Connecting an MCP client

hollerback accepts connections from any MCP client. Auth is a Bearer token; each agent gets its own key.

**Claude CLI:**
```bash
claude mcp add hollerback http://127.0.0.1:7322/mcp \
  --header "Authorization: Bearer $(cat ~/.config/hollerback/agent-keys/default.key)"
```

**Goose Desktop extension:**

| Field | Value |
|-------|-------|
| Extension Name | `hollerback` |
| Type | `Streamable HTTP` *(not STDIO)* |
| Endpoint | `http://127.0.0.1:7322/mcp` |
| Header name | `Authorization` |
| Header value | `Bearer <contents of default.key>` |

**Claude Desktop / Cursor / others:** standard HTTP MCP connection to `http://127.0.0.1:7322/mcp` with `Authorization: Bearer <key>`.

### Multi-agent access ("party line")

Multiple agents can share one Signal number. Each entry under `mcp.agents` in `config.yaml` gets its own named Bearer key:

```yaml
mcp:
  agents:
    - name: claude-cli
      key: <32-byte secret>
    - name: goose-desktop
      key: <different secret>
    - name: cursor
      key: <another secret>
```

All agents see the same contact pool and the same inbound buffer. Calls are authenticated per-agent via `secrets.compare_digest`; `get_signal_identity` returns `"mode": "multi"` so clients can detect they're sharing the line.

**What multi-agent does and doesn't do today:**
- ✅ Coexistence - multiple agents connect without stepping on each other.
- ✅ Per-agent identification - the server knows which key made each call.
- ❌ Per-agent scoping - every agent sees every contact.
- ❌ Inter-agent visibility - agents don't see each other's sends.
- ❌ Per-agent audit - identification is captured internally but not yet in logs.

Enough for a personal "three agents on my desktop share my Signal number" setup. Not enough for multi-tenant.

### Available tools

| Tool | Description |
|------|-------------|
| `get_signal_identity` | Gateway Signal account, mode (`single`/`multi`), `goosed_connected` flag |
| `list_signal_contacts` | Contacts with active sessions (numbers any agent can message) |
| `send_signal_message(phone_number, message)` | Send to a known contact |
| `get_messages(phone_number?, since?)` | Buffered inbound messages; filter by sender or timestamp (ms) |

**Contact gating:** a phone number must pair through hollerback before any agent can message it. The agent cannot cold-call arbitrary numbers.

**Buffer:** `get_messages` reads from an in-memory ring (500 messages per contact). Resets on gateway restart; not persisted.

---

## Security posture

- **Pairing is the default for a reason.** An `open` DM policy gives anyone with your bot number shell-level access through Goose's tool use.
- **Tool approval routes to Signal.** When Goose wants to run a shell command, hollerback sends a yes/no prompt to Signal. You must reply before it proceeds. (Fully wired on the Signal side; activates when goosed exposes the corresponding ACP hook - see limitations.)
- **MCP auth uses per-agent Bearer keys.** Stored in `agent-keys/` (mode 0600), compared in constant time. Each key grants full Signal send/list/read; treat each like a password.
- **hollerback has shell access via Goose.** Treat `~/.config/hollerback/` like root credentials.
- **signal-cli key material** at `~/.local/share/signal-cli` (mode 0700). Do not expose it.
- **goosed discovery** requires a `goosed` binary owned by your UID. Substring matches on process names are rejected.

---

## What doesn't work yet

- **Linux only** - goosed discovery reads `/proc`.
- **Desktop session visibility** - gateway sessions show in Goose Desktop only after a Desktop restart. Upstream fix needed: session-created events from goosed.
- **Real-time message updates** - messages injected into an open session don't refresh the visible Desktop window. Close and reopen as a workaround.
- **goosed port changes on restart** - goosed binds a random port each time Desktop starts. hollerback reconnects every 30s and prefers `GOOSE_PORT`. Inbound messages during the gap are buffered; auto-replies resume on reconnect.
- **No session history replay** - goosed has no history endpoint; gateway restart starts fresh sessions.
- **Tool approval flow** - fully wired on Signal; waiting on goosed to surface permission events via ACP.
- **Use case 2 - inbound on non-Goose agents** - Claude CLI / Cursor can send Signal and read the inbound buffer; they can't receive real-time Signal conversations the way Goose can. Claude CLI isn't a daemon; it's waiting for you to type. Use case 1 requires Goose by design.
- **One session per sender** - no threads/topics within a DM.
- **Text only** - no voice, video, reactions, attachments.
- **signal-cli 0.14.2 quirks** - `editMessage` and `sendReadReceipt` missing from the HTTP daemon; workarounds in `docs/`.

---

## Where this is going

**Short term** - land the things blocking use case 1 from feeling first-class in Goose Desktop:
- Session creation/update events from goosed (so Signal conversations appear in Desktop in real time).
- Session metadata on ACP so conversations carry a display name like "Signal: +16125551234" in the sidebar.
- Approval-resolution ACP hook so the Signal-side tool-approval flow can complete.

Filed as upstream asks; see `docs/UPSTREAM_ASKS.md`.

**Medium term** - the things hollerback owns regardless of upstream direction:
- Persist the inbound message buffer across restarts.
- Per-agent scoping (which contacts each agent can see/message).
- Per-agent audit log with `client_id` attribution on every action.
- Optional streaming replies once `editMessage` lands upstream in signal-cli.

**Long term** - watch `goose gateway`. If Goose opens the pattern to external contributors, hollerback becomes a candidate for a Rust port into the gateway module. If the direction is "channels stay internal," hollerback stays external and keeps working. If ACP-over-HTTP matures enough that "gateway" becomes "any ACP client, any language, any channel," then hollerback slots naturally into that world with minimal changes. We have a design sketch in `docs/GATEWAY_TRAIT_SKETCH.md` for the first case - not a proposal, just a concrete artifact to ground conversation if the goose team ever wants one.

---

## Project structure

```
src/hollerback/
├── acp_client.py      # goosed REST/SSE client
├── approvals.py       # Signal-side tool-approval flow
├── cli.py             # hollerback CLI entry point
├── config.py          # config model + YAML load/save
├── dedup.py           # message deduplication
├── gateway.py         # main loop: receive → session → stream → reply
├── goosed_client.py   # goosed process discovery (/proc + GOOSE_PORT env)
├── message_buffer.py  # in-memory per-contact message buffer
├── mcp_server.py      # MCP HTTP server + per-agent Bearer auth
├── pairing.py         # sender pairing handshake
├── session_map.py     # persistent Signal conversation → session_id map
└── signal_client.py   # signal-cli HTTP client (send, typing, receipts, SSE)

systemd/
├── hollerback.service     # user unit for the gateway
└── signal-cli.service     # reference unit for signal-cli daemon

docs/
├── acp-findings.md              # goosed API contract notes
├── desktop-integration.md       # Desktop session visibility investigation
├── mcp-server-research.md       # MCP direction research and Goose internals
├── GATEWAY_TRAIT_SKETCH.md      # informal design sketch for multi-channel goose gateway
└── UPSTREAM_ASKS.md             # discussion post + issue drafts for aaif-goose/goose
```

---

## Related projects

- [signal-cli](https://github.com/AsamK/signal-cli) - the Signal Messenger CLI hollerback drives.
- [Goose](https://github.com/aaif-goose/goose) - the agent hollerback is built for. Now part of the [Agentic AI Foundation](https://aaif.io/) at the Linux Foundation.
- [Goose Telegram Gateway](https://goose-docs.ai/docs/experimental/remote-access/telegram-gateway) - Goose's own experimental gateway, for Telegram.
- [OpenClaw](https://openclaw.ai/), [Hermes Agent](https://github.com/nousresearch/hermes-agent) - standalone personal-assistant platforms with broad messaging support.... but they're not Goose.
- Alternative signal-mcp projects reviewed: [`rymurr/signal-mcp`](https://github.com/rymurr/signal-mcp), [`retog/signal-mcp`](https://github.com/retog/signal-mcp), [`stefanstranger/signal-mcp-server`](https://github.com/stefanstranger/signal-mcp-server), [`piebro/signal-mcp-client`](https://github.com/piebro/signal-mcp-client).

---

## License

MIT
