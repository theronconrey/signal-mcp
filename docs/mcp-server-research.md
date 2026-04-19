# MCP Server Direction — Research Notes

**Date:** 2026-04-18  
**Branch:** mcp-server (on master as of commit 3616e4c)

---

## Direction

Reframing the project from `goose-signal-gateway` (Goose-specific) to `signal-mcp` (any MCP client). The gateway becomes one consumer; the MCP server is the product.

**Repo renamed:** `theronconrey/goose-signal-gateway` → `theronconrey/signal-mcp`

---

## Architecture Decision

### Auth
- `gateway_secret` generated once during `goose-signal setup`, stored in `config.yaml`
- User pastes secret into Goose Desktop → Extensions → Add custom extension → Request Headers: `X-Gateway-Key: <secret>`
- Same key = one credential, two uses (Signal setup + MCP registration)
- All localhost — no OAuth complexity needed

### Contact Model
- A Signal contact must **initiate the conversation first** (pairing flow)
- Once they've sent a message, a session exists in the session map
- **Session in session map = Goose can message that contact**
- Goose cannot cold-call approved-but-silent numbers

### MCP Tools (proposed)

| Tool | Description |
|------|-------------|
| `get_signal_identity` | Returns the gateway's configured Signal account number |
| `list_signal_contacts` | Returns numbers with active sessions (have initiated conversation) |
| `send_signal_message(recipient, message)` | Send to any number in the session map |

`send_signal_message` validates recipient against session map — can't send to unapproved/uninitiated numbers.

---

## Goose Desktop Session Visibility Research

**Key question:** Can we make a Signal-initiated session appear in the Goose Desktop sidebar?

### How the Desktop manages sessions

1. **No local state file** — sessions are server-side in goosed, not a `projects.json`
2. **Session list sources** (merged in `loadSessions()`):
   - `acpListSessions()` → `GET /sessions` on goosed
   - `"goose:chat-draft-sessions"` localStorage key
   - `"goose:acp-session-metadata"` localStorage key (user-set titles, archive status)
3. **No continuous polling** — `loadSessions()` called once at startup and on explicit triggers
4. **No file watchers** — no `fs.watch` or equivalent
5. **WebSocket via ACP** — Desktop maintains a WebSocket to goosed for `sessionUpdate` notifications

### The binding opportunity

Because `loadSessions()` calls `GET /sessions` on goosed, and our gateway already creates sessions in goosed via `POST /agent/start`, those sessions **are already discoverable** by the Desktop — but only if `loadSessions()` is triggered.

**The gap:** There's no push mechanism. Desktop doesn't know to reload when the gateway creates a new session.

### localStorage approach (fragile but possible)

An external process could write to:
- `"goose:acp-session-metadata"` — set title, metadata for the session
- `"goose:chat-draft-sessions"` — inject a draft session entry

Caveats:
- Electron localStorage is partitioned (`persist:goose`) — writing requires going through Electron IPC or the same browser partition
- After writing, Desktop would need a `"reload-app"` IPC trigger or manual refresh
- This is undocumented and fragile across Goose Desktop versions

### Recommended path

**Phase 1 (now):** Build the MCP server with the three tools. Goose can send Signal messages and list contacts. Session visibility in the sidebar is NOT solved yet.

**Phase 2 (upstream):** File an issue with `block/goose` requesting:
1. goosed to emit a `sessionCreated` WebSocket notification when any client creates a session
2. Desktop to listen for that notification and call `loadSessions()`

This is a small change on their side that solves the problem cleanly without fragile localStorage hacks.

### Relevant Goose source files
- Session store: `/ui/goose2/src/features/chat/stores/chatSessionStore.ts`
- ACP connection: `/ui/goose2/src/shared/api/acpConnection.ts`
- Notification handler: `/ui/goose2/src/shared/api/acpNotificationHandler.ts`
- Session metadata persistence: `/ui/goose2/src/features/chat/lib/sessionMetadataOverlay.ts`
- goosed session routes: `/crates/goose-server/src/routes/session.rs`
- goosed session events: `/crates/goose-server/src/routes/session_events.rs`

---

## signal-cli 0.14.2 Known Quirks (already fixed)

| Method | Status | Workaround |
|--------|--------|------------|
| `editMessage` | -32601 not implemented | Dropped placeholder, send final reply only |
| `sendReadReceipt` | -32601 not implemented | Use `sendReceipt` + `target-timestamps` (hyphenated) |

---

## Next Session Checklist

1. `git checkout mcp-server` (already created, clean branch off master)
2. Add `mcp` package to `pyproject.toml` dependencies
3. Create `src/goose_signal_gateway/mcp_server.py`
   - FastMCP or raw MCP HTTP server
   - Auth middleware: validate `X-Gateway-Key` header against `config.gateway_secret`
   - Tool: `get_signal_identity` → returns `cfg.daemon.account`
   - Tool: `list_signal_contacts` → reads session map, returns numbers with active sessions
   - Tool: `send_signal_message(recipient, message)` → validates against session map, calls `SignalClient.send()`
4. Wire MCP server startup into `Gateway.start()` as a background asyncio task
5. Add `mcp_port` to config (default: 7322)
6. Add `gateway_secret` to config + generate during `goose-signal setup`
7. Update systemd unit if port needs to be exposed
8. Test: register in Goose Desktop, call each tool, verify Signal message arrives
