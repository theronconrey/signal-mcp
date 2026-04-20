# Handoff Report

**Date:** 2026-04-19  
**goosed version:** v1.30.0 (Goose Desktop, Fedora)  
**signal-cli version:** 0.14.2  
**Repo:** theronconrey/hollerback

---

## Status

| Phase | Status | Notes |
|-------|--------|-------|
| 0 — Scaffolding | ✅ Complete | |
| 0.5 — ACP contract verification | ✅ Complete | See `docs/acp-findings.md` |
| 1 — signal-cli client | ✅ Complete | `signal_client.py` |
| 2 — Dedup | ✅ Complete | `dedup.py` |
| 3 — Session map | ✅ Complete | `session_map.py` — file-backed, atomic writes, 0o600 |
| 4 — Pairing | ✅ Complete | `pairing.py` |
| 5 — ACP client | ✅ Complete | `acp_client.py` — see deviations below |
| 6 — Approvals | ✅ Complete | `approvals.py` |
| 7 — Gateway main loop | ✅ Complete | Typing indicators, read receipts, graceful drain |
| 7b — Session metadata & Desktop visibility | ✅ Complete | Documentation only; upstream issue pending |
| 7c — Desktop → Signal forwarding | ⏭ Deferred | Blocked on Desktop session visibility upstream fix |
| 8 — CLI | ✅ Complete | `cli.py` — start, stop, status, logs, doctor, pairing, sessions, setup |
| 9 — systemd units | ✅ Complete | `systemd/` directory; service running on borealis.home |
| 10 — Documentation | ✅ Complete | README rewritten to reflect hollerback direction |
| 11 — End-to-end smoke test | ✅ Complete | Tested live; read receipts, replies, MCP tools all verified |
| 12 — MCP server | ✅ Complete | `mcp_server.py` — bidirectional Signal via MCP |

---

## What's working today

- Signal → Goose: inbound messages create/resume goosed sessions, replies sent back to Signal
- Read receipts (filled double-ticks) sent immediately on message receipt
- Typing indicators while Goose is processing
- Pairing flow for unknown senders
- Per-conversation session locking (serialised per DM)
- Message deduplication
- MCP server on port 7322 with three tools:
  - `get_signal_identity` — returns gateway Signal number
  - `list_signal_contacts` — lists contacts with active sessions
  - `send_signal_message` — sends Signal message from any MCP client
- Auth: `X-Gateway-Key` header validated against `gateway_secret` in config
- systemd user service, enabled and running

---

## signal-cli 0.14.2 quirks

| Method | Status | Workaround |
|--------|--------|------------|
| `editMessage` | -32601 not implemented | Dropped live-edit placeholder; send final reply only |
| `sendReadReceipt` | -32601 not implemented | Use `sendReceipt` + `target-timestamps` (hyphenated) |

---

## Deviations from original spec

### goosed API (Phase 5)

| Spec | Reality | Impact |
|------|---------|--------|
| `initialize` handshake | `GET /status` only | Low — health check works |
| `session/new` accepts metadata | `POST /agent/start` takes `working_dir` only | `display_name` not set |
| `session/load` for history replay | No such endpoint | `session_load()` raises `NotImplementedError` |
| `resolve_permission` | No such endpoint | Approval flow sends Signal prompt but ACP handshake cannot complete |
| `permission_request` notifications | Not surfaced by goosed v1.30.0 | Approval flow implemented but never triggered |

### `manage_goosed` not implemented

Gateway assumes goosed is already running. Auto-spawning goosed as a child process is not implemented.

---

## Open upstream issues to file

1. **Desktop session list does not surface externally-created sessions.**
   goosed sessions created by the gateway appear in `GET /sessions` but not in Desktop's sidebar. Desktop reads local state only; no polling or WebSocket notification for externally-created sessions. Fix: goosed should emit a `sessionCreated` WebSocket event; Desktop should call `loadSessions()` on receipt.

2. **`POST /agent/start` has no metadata field.**
   A `display_name` or `tags` field would produce readable session names in Desktop once issue #1 is resolved.

3. **No `resolve_permission` endpoint.**
   Approval flow is fully implemented on the Signal side but cannot complete the ACP handshake.

---

## Next steps

1. File the three upstream issues against `block/goose`
2. Add `gateway_secret` generation to the `hollerback setup` wizard (currently requires manual config edit)
3. Phase 7c — Desktop → Signal forwarding (blocked on upstream session visibility fix)
4. Consider publishing to PyPI once setup wizard handles MCP config automatically
