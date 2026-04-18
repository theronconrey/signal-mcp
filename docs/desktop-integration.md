# Desktop Integration

## What works

Sessions created by the gateway via `POST /agent/start` are fully functional in
goosed. Messages route correctly, replies stream, and token counts accumulate.
The gateway and Goose Desktop can operate against the same goosed instance
simultaneously without conflict.

## What doesn't (yet)

Gateway-created sessions **do not appear in Goose Desktop's session sidebar.**

### Root cause

Desktop tracks sessions via
`~/.local/share/goose/projects.json` — a local state file it manages itself.
The session list reflects that local state, not a live query against
`GET /sessions` on goosed. Sessions created externally (by the gateway, or any
other ACP client) are invisible to the sidebar even though goosed knows about
them and routes to them correctly.

### Upstream issue

This is a Desktop UX gap, not a gateway bug. The recommended fix is for Desktop
to poll `GET /sessions` and surface externally-created sessions in its list.

**Upstream issue to file:** `block/goose` — "Desktop session list should surface
sessions created by external ACP clients."

Suggested fields for the issue:
- `GET /sessions` already returns all sessions with full metadata
- Session objects from the gateway are structurally identical to Desktop-created
  ones (`session_type: user`, same fields)
- The gateway sets a readable name automatically (goosed names sessions from the
  first message); a `display_name` metadata field would make tagging richer
- Proposed behaviour: Desktop polls `/sessions` on a short interval (or reacts
  to a server-sent event) and adds externally-created sessions to the sidebar
  with a visual indicator (e.g. a Signal icon or a `[signal]` badge)

No gateway-side change is needed once upstream lands. The gateway already
produces correct metadata.

---

## Smoke test: confirming a session exists in goosed

Even though the Desktop sidebar doesn't show gateway sessions, you can confirm
they exist and inspect them:

### 1. Start the stack

```bash
# Terminal 1 — signal-cli daemon
signal-cli --config ~/.local/share/signal-cli daemon --http 127.0.0.1:8080

# Terminal 2 — gateway
uv run main.py --account +1XXXXXXXXXX
```

### 2. Send a Signal message

From your phone (or another Signal client), DM the bot account:

```
Hello, are you there?
```

The gateway logs should show:

```
Signal ← +1YYYYYYYYYY: 'Hello, are you there?'
Created session 20260418_5 for +1YYYYYYYYYY
Signal → +1YYYYYYYYYY: '...'   ← streaming reply
```

### 3. Confirm the session exists in goosed

```bash
SECRET=$(cat /proc/$(pgrep goosed)/environ | tr '\0' '\n' | grep GOOSE_SERVER__SECRET_KEY | cut -d= -f2)
PORT=$(ss -tlnp | grep goosed | awk '{print $4}' | cut -d: -f2)

curl -sk -H "X-Secret-Key: $SECRET" https://127.0.0.1:$PORT/sessions | python3 -m json.tool
```

You should see the gateway session in the list:

```json
{
  "sessions": [
    {
      "id": "20260418_5",
      "name": "Hello, are you there?",
      "working_dir": "/home/youruser",
      ...
    }
  ]
}
```

### 4. Confirm the exchange is navigable

```bash
# List messages in the session (if goosed exposes a history endpoint)
curl -sk -H "X-Secret-Key: $SECRET" \
  "https://127.0.0.1:$PORT/sessions/20260418_5/messages" | python3 -m json.tool
```

> Note: goosed v1.30.0 does not expose a `/sessions/{id}/messages` endpoint.
> History is stored on disk at `~/.local/share/goose/sessions/` as JSONL.
> You can read it directly:
>
> ```bash
> cat ~/.local/share/goose/sessions/20260418_5.jsonl | python3 -m json.tool
> ```

### 5. Open Goose Desktop

Launch Goose Desktop. The session sidebar will **not** show the gateway session
(see above). However, you can manually load it:

1. Open Desktop developer tools or the session switcher if available
2. The session JSONL file is at `~/.local/share/goose/sessions/20260418_5.jsonl`

Once the upstream Desktop issue is resolved, this step will simply be: open
Desktop, see the Signal session in the sidebar, click it.

---

## Current status summary

| Feature | Status |
|---------|--------|
| Messages route Signal → Goose → Signal | ✅ Working |
| Sessions visible in `GET /sessions` | ✅ Working |
| Sessions appear in Desktop sidebar | ❌ Desktop UX gap (upstream issue pending) |
| Session history on disk (JSONL) | ✅ Working |
| Concurrent Desktop + Signal on same session | ✅ Working (no conflicts observed) |
