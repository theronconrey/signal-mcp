# hollerback

## What this is

A Python service bridging Signal Messenger to AI agents via MCP. Signal conversations become live goosed sessions (when goosed is available); any MCP client (Claude CLI, Goose Desktop, Cursor, etc.) can also send messages and read inbound messages directly via the MCP tools.

This is a proof-of-concept prototype.

## Key findings (read before modifying)

Full goosed API contract: `docs/acp-findings.md`. Critical points:

- goosed runs **HTTPS** with a self-signed cert. Always `verify=False`.
- Auth header is `X-Secret-Key`, not `Authorization: Bearer`.
- Port is dynamic per Goose Desktop launch. Discovered via `GOOSE_PORT` env var (preferred) or `/proc` socket scan fallback.
- New sessions need `POST /agent/update_provider` before they can reply.
- signal-cli in daemon mode: use SSE at `GET /api/v1/events`. The `receive` JSON-RPC method does not work in daemon mode.

## MCP auth

Auth header is `Authorization: Bearer <agent_key>` (not `X-Gateway-Key` — that was the old scheme). Keys are per-agent entries under `mcp.agents` in `config.yaml`. Backwards-compatible: a legacy `mcp.secret` field is migrated to a single `default` agent on load.

## MCP usage (HTTP/SSE transport)

The server uses the MCP streamable-HTTP transport. Every session requires a two-step handshake before calling tools:

1. **Initialize** — `POST /mcp` with `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"...","version":"1.0"}}}`. Include `Accept: application/json, text/event-stream`. The response header `mcp-session-id` contains the session token.
2. **Call tools** — subsequent `POST /mcp` requests must include `mcp-session-id: <token>` from step 1.

## MCP tools

| Tool | Purpose |
|------|---------|
| `get_signal_identity` | Gateway account number, mode, goosed status |
| `list_signal_contacts` | Active paired contacts — the valid recipients for `send_signal_message` |
| `get_messages` | Buffered inbound messages; filter by `phone_number` or `since` (ms timestamp) |
| `send_signal_message` | Send to a paired contact (must appear in `list_signal_contacts`) |

**Sending to the owner:** The gateway's own Signal account is `daemon.account` in `config.yaml` — that is the *gateway's* number, not the owner's. The owner's number appears in `list_signal_contacts` after they pair. Always call `list_signal_contacts` first to find valid recipients.

## Message formatting contract

Signal renders plain text only. `send_signal_message` rejects messages that
contain structural Markdown (headings, code fences, multi-line bullet lists,
`[text](url)` link syntax) with an error telling the agent to rewrite as prose.
This is enforcement-at-the-tool-boundary: every MCP client sees the same
description-in-context plus server-side lint, so the rule does not depend on
any one agent's memory. Message content is never modified — length and inline
emphasis pass through untouched. See `src/hollerback/signal_lint.py`. Extra
owner-specific style guidance can be appended via `signal.style_prompt` in
`config.yaml` (rendered into the tool description).

## Graceful degradation

The gateway starts and operates without goosed. When goosed is unavailable:
- Inbound messages are buffered in `MessageBuffer` (in-memory, 500 msg/contact cap).
- `get_messages` MCP tool returns buffered messages.
- Auto-replies to Signal are held until goosed reconnects.
- `_goosed_reconnect_loop` polls every 30 seconds and wires up `ApprovalCoordinator` automatically on reconnect.

## Environment

- Linux (uses `/proc` for goosed discovery fallback)
- Python 3.12+ managed with `uv`
- signal-cli running as HTTP daemon at `127.0.0.1:8080`
- Goose Desktop optional (gateway runs without it)

## Running

```bash
uv sync
uv run hollerback setup   # first time
uv run hollerback start   # foreground
uv run hollerback start --detach   # systemd user unit
```
