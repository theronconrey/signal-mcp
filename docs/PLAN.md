# Goose Signal Gateway — Implementation Plan (ACP Edition)

**For:** Claude Code, executing on a Fedora workstation.
**Goal:** A Python service that bridges Signal Messenger to a running `goosed` via the Agent Client Protocol (ACP), so Signal conversations are first-class Goose sessions visible in both Signal and Goose Desktop.
**Scope:** Full depth — dedup, reconnect, pairing, approvals, streaming, Desktop-visible sessions.
**Form factor:** A standalone `goose-signal-gateway` Python package that runs alongside `goosed`. **Not** an in-tree Goose contribution; validation prototype for the design. If the design holds up, a future Rust port can land in-tree.

---

## Ground rules for the implementer

Read this whole file before writing any code. Do not improvise architectural decisions — if something is ambiguous, stop and ask. The user who handed you this plan has context from the design conversation; you do not. When behavior is uncertain, verify against a live `goosed` (Phase 0.5 below) rather than guessing. Note any deviations in `HANDOFF_REPORT.md` at the end.

**Environment assumptions:**

- Fedora (42+). Use `dnf`, never `apt`.
- Python 3.12+ (Fedora 42 ships 3.13).
- User has `signal-cli` installed and a Signal account ready to link.
- User has `goose` installed. A running `goosed` instance is the integration target — either the one Desktop launches, or one the gateway manages itself (see Phase 0.5).
- Linux systemd for service management.

**Non-goals (do not build these):**

- Rust implementation. This is Python.
- In-tree Goose contribution (touching `crates/` or `ui/desktop/`). Standalone package.
- Subprocess-driving mode as a fallback. ACP-only. If goosed is unavailable, the gateway fails to start with a clear error — no silent fallback to `goose run`.
- Multi-channel abstraction (Discord, WhatsApp, etc.). Signal only.
- Web UI. CLI only.
- Tests against Signal's live servers. Mock signal-cli in unit tests.

**Reference implementations and docs to read before coding:**

- `https://agentclientprotocol.com` — ACP protocol spec. Primary reference.
- `https://github.com/block/goose/tree/main/crates/goose-acp` — the goose-acp crate. Authoritative for what `goosed`'s ACP server actually implements today (may be ahead of or behind the spec).
- `https://github.com/block/goose/issues/6642` — the ACP migration tracking issue. Read the method list and the SSE-over-HTTP transport notes.
- `https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/signal.py` — the closest real-world Signal adapter. Study its dedup and reconnect; we deliberately copy-shape but not code.
- `https://github.com/NousResearch/hermes-agent/issues/405` — Hermes's Signal design issue, lists the edge cases.

Fetch these once at the start, summarize what you learned in scratch notes, move on.

---

## Architecture at a glance

```
┌──────────────┐   Signal protocol   ┌──────────────┐
│ User's phone │◄────────────────────┤Signal servers│
└──────────────┘                     └──────┬───────┘
                                            │
                                            ▼
                           ┌────────────────────────────────┐
                           │ signal-cli daemon (HTTP mode)  │
                           │ localhost:8080                 │
                           └────┬───────────────────────┬───┘
                    SSE inbound │                       │ JSON-RPC out
                                ▼                       ▲
                           ┌────────────────────────────────┐
                           │   goose-signal-gateway (us)    │
                           │                                │
                           │   SseReader ──► Router ──► Sess│
                           │                            │   │
                           │                            ▼   │
                           │                      ACP Client│
                           └──────────────────┬─────────────┘
                                              │ ACP over HTTP+SSE
                                              ▼
                           ┌────────────────────────────────┐
                           │          goosed                │
                           │     (ACP server, sessions)     │
                           └──────────────┬─────────────────┘
                                          ▲
                                          │ same ACP endpoint
                                          │
                           ┌──────────────┴─────────────────┐
                           │       Goose Desktop            │
                           │  (listing, viewing, replying)  │
                           └────────────────────────────────┘
```

**Key invariants:**

1. **The gateway owns no agent state.** Every conversation is an ACP session in `goosed`. The gateway stores one mapping: `{signal_conversation → acp_session_id}`. Everything else (history, model, tools, MCP extensions) lives in `goosed`.
2. **Sessions are visible to Desktop by construction.** Because they live in `goosed`, any ACP client connected to the same `goosed` sees them. Session metadata tags them as `source: signal` so Desktop can display them distinctly.
3. **Approvals use ACP's permission flow.** Not PTY scraping, not stdout parsing. ACP fires a permission-request notification; the gateway translates to a Signal approval message; the user's "yes"/"no" reply resolves the permission via an ACP call.

---

## Repository layout

Create at `~/code/goose-signal-gateway/` (confirm with user once).

```
goose-signal-gateway/
├── pyproject.toml
├── README.md
├── LICENSE                           # MIT
├── .gitignore
├── .python-version                   # 3.12
├── src/
│   └── goose_signal_gateway/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py                    # click-based CLI
│       ├── config.py                 # pydantic v2 config
│       ├── signal_client.py          # signal-cli JSON-RPC + SSE
│       ├── acp_client.py             # ACP-over-HTTP client
│       ├── session_map.py            # persisted {signal_conv → acp_session_id}
│       ├── dedup.py                  # message dedup cache
│       ├── pairing.py                # DM pairing handshake
│       ├── approvals.py              # ACP permission → Signal approval bridge
│       ├── forwarder.py              # (Phase 7c) desktop→signal forwarding
│       ├── gateway.py                # main loop
│       ├── doctor.py                 # diagnostics
│       ├── logging_setup.py
│       └── errors.py
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_dedup.py
│   ├── test_pairing.py
│   ├── test_signal_client.py
│   ├── test_acp_client.py
│   ├── test_session_map.py
│   ├── test_approvals.py
│   ├── test_gateway.py
│   └── test_forwarder.py
├── systemd/
│   ├── goose-signal-gateway.service
│   └── signal-cli.service            # reference unit for signal-cli
└── scripts/
    ├── dev-signal-mock.py            # fake signal-cli for local testing
    └── dev-acp-mock.py               # fake goosed ACP server for local testing
```

---

## Tech stack (pinned)

- **Python 3.12+**, managed with `uv` (`dnf install uv` or `pipx install uv`).
- **`httpx`** — HTTP + SSE client for both signal-cli and ACP.
- **`click`** — CLI framework.
- **`pydantic` v2** — config parsing and validation.
- **`PyYAML`** — config format.
- **`rich`** — terminal UI for setup wizard and doctor.
- **`qrcode`** — terminal QR rendering for Signal linking.
- **`pytest`** + **`pytest-asyncio`** + **`respx`** — testing.
- **`ruff`** — lint + format.
- **`mypy --strict`** — type checking on `src/`.

No other dependencies without a commit-message justification.

---

## Config file shape

Canonical location: `~/.config/goose-signal-gateway/config.yaml`. Honor `$XDG_CONFIG_HOME`.

```yaml
# ~/.config/goose-signal-gateway/config.yaml
gateway:
  log_level: info
  log_file: ~/.local/state/goose-signal-gateway/gateway.log

daemon:
  url: http://127.0.0.1:8080
  account: "+46701234567"
  start_daemon: false                     # if true, gateway supervises signal-cli
  signal_cli_path: /usr/bin/signal-cli

acp:
  url: http://127.0.0.1:3456               # goosed ACP endpoint
  auth_token_file: ~/.config/goose/acp_token   # optional; path to token file
  manage_goosed: false                     # if true, gateway starts its own goosed
  goosed_path: /usr/bin/goosed             # only if manage_goosed: true
  goosed_port: 3456                        # only if manage_goosed: true
  session_map_path: ~/.local/state/goose-signal-gateway/sessions.json

access:
  dm_policy: pairing                       # pairing | allowlist | open
  allowed_users:
    - "+46709998888"
  groups:
    enabled: false
    require_mention: true
    allowed_ids: []

behavior:
  home_conversation: "+46709998888"
  approval_timeout_minutes: 30
  reply_streaming: true
  stream_edit_interval_ms: 500
  stream_edit_char_threshold: 80
  deliver_errors: true
  typing_indicator: true

forwarding:
  desktop_to_signal: false                 # Phase 7c; disabled by default
  loop_guard_ttl_seconds: 120

transport:
  sse_idle_timeout_seconds: 120
  reconnect_backoff_min_seconds: 2
  reconnect_backoff_max_seconds: 60
  dedup_ttl_seconds: 60
  dedup_max_entries: 10000

pairing:
  code_ttl_minutes: 60
  max_pending: 3
  storage: ~/.local/state/goose-signal-gateway/pairing.json
```

**Config validation rules** (enforce in `config.py`):

1. `access.dm_policy: open` requires CLI flag `--i-accept-the-risk-of-open-dms` at start. Hard error otherwise.
2. `access.dm_policy: allowlist` with empty `allowed_users` refuses to start.
3. `daemon.account` must be E.164 (`+` then 7–15 digits).
4. `daemon.start_daemon: true` requires an existing, executable `signal_cli_path`.
5. `acp.manage_goosed: true` requires existing, executable `goosed_path` and a free `goosed_port`.
6. Exactly one of `acp.url` (external goosed) or `acp.manage_goosed: true` must be specified usefully — if both, log a warning and prefer external.
7. Expand `~` and `$VARS` in all path fields on load.

---

## Implementation phases

Work through in order. Commit at the end of each phase with the phase name.

### Phase 0: Scaffolding ✅ COMPLETE (2026-04-18)

1. `uv init`, Python 3.14 (3.12+ required; system has 3.14).
2. Created `src/goose_signal_gateway/` package with `goosed_client.py`, `signal_client.py`, `session_store.py`, `gateway.py`.
3. `uv add httpx aiohttp`.
4. `main.py` entrypoint.

**Commit:** `phase 0: scaffolding`

### Phase 0.5: Verify the ACP contract against reality ✅ COMPLETE (2026-04-18)

**Do this before Phase 5.** It's the single most important de-risking step in the plan.

Start a real `goosed` (`goosed start` or equivalent — check `goose --help` and `goosed --help` for the right invocation). With a minimal Python script using `httpx`, exercise the ACP endpoint and record findings in `docs/acp-findings.md`:

1. **Initialize handshake.** POST `/acp` with `{"method": "initialize", ...}`. Confirm the response shape, the `Acp-Session-Id` header behavior, and the SSE stream semantics.
2. **session/new.** Create a session. Does it accept a `metadata` field? What fields are in the params? Capture the exact shape.
3. **Session metadata tagging.** Set `source: "signal"`, `conversation: "+46700000001"`, `display_name: "Signal: +46700000001"` in whatever field ACP supports. If ACP has no metadata field today, note this explicitly — Phase 7b may need to file an upstream issue or PR against `goose-acp`.
4. **session/prompt.** Send a trivial prompt. Record the notification stream: `AgentMessageChunk`, `AgentThoughtChunk`, `ToolCall`, permission-request, whatever comes. Save example payloads in `docs/acp-findings.md`.
5. **Permission flow.** Ask Goose to do something that triggers approval (e.g., run a shell command with `GOOSE_MODE=approve` or whatever the current flag is). Capture the permission-request notification shape and the method to resolve it.
6. **session/load.** Create a session, send a prompt, disconnect, reconnect, load the session, confirm history replays.
7. **Desktop visibility.** Start Goose Desktop. Does your ACP-created session appear in its session list? If yes, confirm metadata is visible. If no, document what's missing — this is critical input for Phase 7b.
8. **Authentication.** Does goosed require a token? If yes, where does it live and how is it presented (header, param)?

Output: a concrete `docs/acp-findings.md` with actual request/response samples. Every subsequent phase references this doc. If any of the above diverges from what this plan assumes, **update the plan in-tree before proceeding** and note the divergence in `HANDOFF_REPORT.md`.

**Findings summary (see `docs/acp-findings.md` for full detail):**
- goosed is NOT ACP — it's a proprietary REST/SSE API (not the ACP protocol at agentclientprotocol.com)
- Transport: HTTPS self-signed cert, auth via `X-Secret-Key` header (not Bearer)
- Port discovery: `/proc/<pid>/fd` socket inode matching against `/proc/net/tcp`
- Secret: `GOOSE_SERVER__SECRET_KEY` env var in goosed process, fresh per Desktop launch
- Key endpoints: `GET /status`, `POST /agent/start`, `POST /agent/update_provider`, `POST /reply` (SSE), `GET /sessions`
- Streaming: multiple `Message` events per turn, same `message.id`, concatenate text chunks
- New sessions need `POST /agent/update_provider` before they can reply
- Full round-trip verified: create session → configure provider → send message → stream reply ✅

**Commit:** `phase 0.5: acp contract findings`

### Phase 1: signal-cli client

Implement `signal_client.py`.

The signal-cli daemon in `--http` mode exposes:
- **JSON-RPC 2.0 over HTTP POST** at `/api/v1/rpc` for outbound.
- **SSE stream** at `/api/v1/events` for inbound.

Single async `SignalClient` class:

```python
class SignalClient:
    async def __aenter__(self) -> "SignalClient": ...
    async def __aexit__(self, *exc) -> None: ...

    async def send_message(
        self, recipient: str | None = None, group_id: str | None = None,
        message: str = "", attachments: list[Path] | None = None,
        quote_timestamp: int | None = None, quote_author: str | None = None,
    ) -> int: ...  # returns sent timestamp

    async def send_typing(self, recipient, group_id, stop: bool = False) -> None: ...
    async def send_reaction(self, recipient, target_author, target_timestamp, emoji) -> None: ...
    async def edit_message(self, recipient, group_id, target_timestamp, new_text) -> None: ...
    async def download_attachment(self, attachment_id: str) -> bytes: ...
    async def get_account_details(self) -> dict: ...

    async def stream_events(self) -> AsyncIterator[SignalEvent]: ...
```

**Behaviors:**

- **SSE reconnect loop.** Exponential backoff between `reconnect_backoff_min_seconds` and `reconnect_backoff_max_seconds`. Reset on successful message. Log every reconnect at INFO.
- **SSE idle watchdog.** If no event (including keepalive `:ping` comments) within `sse_idle_timeout_seconds`, force-close and reconnect. This is non-negotiable — Hermes hit this bug repeatedly.
- **JSON-RPC errors.** Typed exceptions in `errors.py`: `RateLimitError`, `UntrustedIdentityError`, `NotRegisteredError`, `SignalClientError` (catch-all). **Never** auto-retry `UntrustedIdentityError` — that's a human-in-the-loop situation.
- **Attachment ID validation.** Regex `^[A-Za-z0-9_-]{1,128}$` before interpolating into URL paths. Anything else raises. Prevents SSRF.

**Event normalization** — all events become a `SignalEvent`:

```python
@dataclass(frozen=True)
class SignalEvent:
    kind: Literal["message", "stream_opened", "stream_closed", "unknown"]
    timestamp: int
    source_number: str | None
    source_uuid: str | None
    group_id: str | None
    text: str | None
    attachments: list[AttachmentRef]
    quote: QuoteRef | None
    raw: dict
```

Handle these envelope types: `receipt` (drop), `typing` (drop v1), `dataMessage` (emit), `syncMessage.sentMessage` (emit; caller dedups own echoes), `editMessage` (log, drop v1), `reaction` (log, drop v1).

**Tests** (using `respx` to mock httpx):

- Send returns the timestamp from the RPC response.
- Attachment ID validation rejects `../`, absolute paths, unicode tricks.
- SSE reader yields events in order.
- SSE reader reconnects after HTTP 500.
- SSE reader force-reconnects on idle timeout.
- `syncMessage` with our own timestamp is emitted with `source` = our account.

**Commit:** `phase 1: signal-cli client`

### Phase 2: Dedup

Implement `dedup.py`.

```python
class MessageDeduplicator:
    def __init__(self, ttl_seconds: int, max_entries: int) -> None: ...
    def seen(self, timestamp: int, text: str, source: str) -> bool: ...
    def remember_outbound(self, timestamp: int, text: str, our_account: str) -> None: ...
```

- Hash: `sha256(f"{timestamp}:{text}:{source}").hexdigest()`.
- `collections.OrderedDict` with LRU eviction at `max_entries`.
- Prune entries older than `ttl_seconds` on each call.
- Wrap with `asyncio.Lock`.

**Tests:**

- `seen` False on first, True on second.
- Expires after TTL.
- Evicts oldest when max exceeded.
- `remember_outbound` + `seen` returns True on match.
- Different `source`, same text+timestamp, treated as distinct.

**Commit:** `phase 2: dedup`

### Phase 3: Session map

Implement `session_map.py`.

Far simpler than the original plan's `sessions.py` — the gateway doesn't own conversation state, just a persistent `{signal_conversation_key → acp_session_id}` mapping.

```python
@dataclass(frozen=True)
class ConversationKey:
    kind: Literal["dm", "group"]
    identifier: str   # E.164 or UUID for DM; group_id for group

    def as_str(self) -> str:
        return f"{self.kind}:{self.identifier}"

class SessionMap:
    def __init__(self, path: Path) -> None: ...
    async def get(self, key: ConversationKey) -> str | None: ...
    async def set(self, key: ConversationKey, acp_session_id: str) -> None: ...
    async def delete(self, key: ConversationKey) -> None: ...
    async def all(self) -> dict[str, str]: ...
```

Backed by a JSON file on disk. Atomic writes (temp + rename). Loaded once on startup, kept in memory, persisted on change.

Per-conversation serialization still matters — two messages in the same DM process in order, different DMs can process concurrently. Implement with an `asyncio.Lock` per conversation key, acquired around the full "send to ACP → stream response → edit Signal" cycle. The gateway's dispatcher holds a `dict[str, asyncio.Lock]`, lazily created.

**Tests:**

- Mapping round-trips through disk.
- Atomic write: simulate crash mid-write; file remains valid.
- Two messages to the same DM serialize; to different DMs run concurrently.

**Commit:** `phase 3: session map`

### Phase 4: Pairing

Implement `pairing.py`. Semantics unchanged from the original plan.

- `dm_policy: pairing`: unknown senders get a code reply; bot ignores further messages from them until `goose-signal pairing approve <code>` is run.
- Codes: 6 alphanumeric chars via `secrets.token_urlsafe(4)`, uppercased.
- TTL per `pairing.code_ttl_minutes`.
- Max `pairing.max_pending` concurrent codes per sender.
- Persisted to `pairing.storage` (JSON, atomic writes). Union-ed with `allowed_users` on lookup.

```python
class PairingStore:
    def __init__(self, path: Path, code_ttl: timedelta, max_pending: int): ...
    def is_approved(self, source: str) -> bool: ...
    def request_code(self, source: str) -> str | None: ...
    def approve(self, code: str) -> str | None: ...
    def list_pending(self) -> list[PendingCode]: ...
    def deny(self, code: str) -> bool: ...
    def revoke_approval(self, source: str) -> bool: ...
```

Signal-side pairing message:

```
This bot requires pairing. Ask the operator to run:

    goose-signal pairing approve ABCD23

Your pairing code expires in 60 minutes.
```

Second message from the same unknown sender before approval does NOT issue a new code — return a brief "code already issued" reply.

Operator CLI:

```bash
goose-signal pairing list
goose-signal pairing approve ABCD23
goose-signal pairing deny ABCD23
goose-signal pairing revoke +46701234567
```

**Tests:**

- Unknown sender gets a code; repeat before approval gets "already issued".
- Approved sender bypasses.
- Expired codes invalid.
- Persistence round-trip.
- Atomic write survives mid-write crash.

**Commit:** `phase 4: pairing`

### Phase 5: ACP client

Implement `acp_client.py`. **Read `docs/acp-findings.md` before writing this.** The exact shapes below are placeholders — use what Phase 0.5 discovered.

```python
class AcpClient:
    def __init__(self, url: str, auth_token: str | None = None) -> None: ...

    async def __aenter__(self) -> "AcpClient": ...
    async def __aexit__(self, *exc) -> None: ...

    async def initialize(self) -> InitializeResult:
        """Handshake; negotiate capabilities."""

    async def session_new(
        self,
        cwd: str,
        mcp_servers: list[dict] | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Return the ACP session_id."""

    async def session_load(self, session_id: str) -> AsyncIterator[SessionNotification]:
        """Resume; yields history replay notifications, then stays open."""

    async def session_prompt(
        self,
        session_id: str,
        prompt: str,
    ) -> AsyncIterator[SessionNotification]:
        """Send a user message; stream assistant notifications until complete."""

    async def resolve_permission(
        self,
        session_id: str,
        request_id: str,
        allow: bool,
    ) -> None:
        """Answer a permission-request notification."""

    async def list_sessions(self) -> list[SessionSummary]:
        """Used by the forwarder (Phase 7c) and doctor."""
```

Notification types (again, confirm exact field names via Phase 0.5):

```python
@dataclass(frozen=True)
class SessionNotification:
    kind: Literal[
        "agent_message_chunk",
        "agent_thought_chunk",
        "user_message_chunk",         # for session/load replays
        "tool_call",
        "tool_result",
        "permission_request",
        "session_complete",
    ]
    session_id: str
    payload: dict
```

**Behaviors:**

- **Connection lifecycle.** `__aenter__` calls `initialize` and holds an auth token. `__aexit__` closes cleanly.
- **Streaming.** Each `session/prompt` and `session/load` opens its own SSE stream (per the ACP migration notes). Parse notifications; yield typed events.
- **Reconnect.** If the SSE stream drops mid-prompt, log and raise `AcpStreamInterruptedError` — the gateway's outer loop decides whether to retry. Do not silently reconnect mid-prompt; the caller needs to know.
- **Session creation metadata.** Always set:
  ```python
  metadata = {
      "source": "signal",
      "source_conversation": key.as_str(),
      "display_name": f"Signal: {key.identifier}",
  }
  ```
  If Phase 0.5 found ACP lacks a metadata field, temporarily encode into the session name or wherever the display field is, and flag this prominently in `HANDOFF_REPORT.md`.
- **Goosed discovery.** If `acp.manage_goosed: true`, fork `goosed` as a child process on the configured port; wait for it to be reachable (retry `initialize` for up to 30 seconds); register SIGTERM handler to stop it cleanly on gateway exit.
- **Goosed authentication.** If `acp.auth_token_file` exists, read and present it. The exact header/param comes from Phase 0.5.

**Streaming cadence into Signal** — this is the bridge between `AcpClient` and `SignalClient`. The gateway orchestrates:

1. Start: send typing indicator, send placeholder message ("…"), record its timestamp.
2. Receive `agent_message_chunk` events: append to buffer.
3. Every `stream_edit_interval_ms` or every `stream_edit_char_threshold` new chars (whichever first), edit the placeholder.
4. On `session_complete`: send final edit with full text; stop typing.
5. On `tool_call` with permission-request: see Phase 6.
6. Skip `agent_thought_chunk` in v1 (configurable later).

**Tests** (using `respx` to mock ACP):

- `initialize` returns capabilities.
- `session_new` returns a session_id; metadata is passed through.
- `session_prompt` yields chunks in order.
- SSE drop mid-prompt raises `AcpStreamInterruptedError`.
- Auth token is sent when configured.
- `manage_goosed: true` spawns and cleans up the child process.

**Commit:** `phase 5: acp client`

### Phase 6: Approvals

Implement `approvals.py`.

**Much smaller than the original plan's approach** — no PTY, no pattern matching. Pure ACP.

When the ACP client yields a `permission_request` notification:

1. Extract the permission details (tool name, arguments, request_id).
2. Send a dedicated Signal message:
   ```
   ⚠️  Goose wants to run:
       <tool name>
       <arguments summary>

   Reply "yes" to approve, "no" to deny.
   Expires in 30 minutes.
   ```
3. Register a pending approval keyed by `session_id` + `request_id`.
4. Wait up to `approval_timeout_minutes` for a matching Signal reply.
5. Parse reply: case-insensitive `y|yes` → allow, `n|no` → deny, anything else → ignore (send "Waiting for yes/no").
6. Call `AcpClient.resolve_permission(session_id, request_id, allow=…)`.
7. Resume normal streaming.

```python
class ApprovalCoordinator:
    def __init__(self, signal: SignalClient, acp: AcpClient, timeout: timedelta): ...

    async def request(
        self,
        session_id: str,
        request_id: str,
        signal_conversation: ConversationKey,
        tool_name: str,
        arguments: dict,
    ) -> bool:
        """Send approval prompt, await reply, resolve via ACP. Returns granted."""

    async def handle_reply(
        self,
        signal_conversation: ConversationKey,
        text: str,
    ) -> bool:
        """If there's a pending approval for this conversation, consume and resolve.
        Return True if this reply was consumed as an approval answer."""

    async def handle_external_resolution(
        self,
        session_id: str,
        request_id: str,
        allow: bool,
    ) -> None:
        """Called when the forwarder detects Desktop resolved a permission that
        was being awaited on Signal. Notify Signal and cancel the pending wait."""
```

**Critical edge case** — if the user has Desktop open on the same session and answers the permission there, the gateway needs to see it happen and cancel its Signal-side wait, reporting "This was answered in Desktop." The ACP server should fire a resolution event or the permission state changes; watch for it. (Phase 0.5 verifies what happens; if ACP doesn't surface resolutions to observers, this becomes a polling loop on permission state.)

**Tests:**

- Permission request → Signal message sent.
- "yes" reply resolves allow.
- "no" reply resolves deny.
- Timeout resolves deny, sends timeout message.
- Unrelated reply during wait sends "Waiting for yes/no".
- External resolution cancels pending wait.

**Commit:** `phase 6: approvals`

### Phase 7: Gateway main loop

Implement `gateway.py`.

```python
class Gateway:
    def __init__(self, config: Config): ...

    async def run(self) -> None:
        # 1. Open SignalClient. Verify account reachable.
        # 2. Open AcpClient. Initialize. Confirm goosed is up.
        # 3. Load SessionMap from disk.
        # 4. Start forwarder task (if enabled).
        # 5. Send startup notification to home_conversation.
        # 6. Main loop:
        #    async for event in signal.stream_events():
        #        await self._handle(event)
        # 7. On SIGTERM/SIGINT: drain pending prompts, close streams, stop goosed
        #    if managed, send shutdown notification, exit.

    async def _handle(self, event: SignalEvent) -> None:
        # a. Non-message events: log, return.
        # b. Echo of our own send (syncMessage): drop.
        # c. Dedup check: drop if seen.
        # d. Is this a reply to a pending approval? Route to ApprovalCoordinator.
        # e. Access control:
        #    - group: check groups.enabled + allowed_ids + require_mention.
        #    - DM: check allowlist; if pairing needed, handle via PairingStore.
        # f. Dispatch to conversation handler (per-conversation lock).

    async def _run_conversation(
        self, key: ConversationKey, message: SignalEvent
    ) -> None:
        # 1. Look up ACP session_id in SessionMap. Create via session/new if absent.
        # 2. Send typing indicator on Signal.
        # 3. Send placeholder ("…") on Signal, record timestamp.
        # 4. Call acp.session_prompt(session_id, message.text).
        # 5. Consume notifications:
        #    - agent_message_chunk: accumulate, rate-limited edit.
        #    - permission_request: await approval via ApprovalCoordinator.
        #    - tool_call / tool_result: log (and optionally show in Signal; decide
        #      in Phase 0.5 based on how noisy these are).
        #    - session_complete: final edit, stop typing.
        # 6. Any exception: send an error message if deliver_errors, log regardless.
```

Signal handlers for `SIGTERM` / `SIGINT`:
- Stop accepting new Signal events.
- Cancel in-flight prompts (they'll send "cancelled" messages).
- Close SSE streams on both sides.
- If `acp.manage_goosed: true`, stop goosed.
- Send shutdown notification to `home_conversation`.
- Exit.

**Tests:**

- End-to-end with mock signal-cli + mock ACP: message in, prompt sent, chunks stream back, Signal sees the reply.
- Two messages same DM: processed in order.
- Two messages different DMs: concurrent.
- Unknown sender triggers pairing flow.
- Duplicate event dropped.
- ACP stream interrupted: error reported to Signal, logged.

**Commit:** `phase 7: gateway main loop`

### Phase 7b: Session metadata & Desktop visibility

Goal: a Signal conversation appears in Goose Desktop's session list, tagged distinctly, with a readable name, and its history is navigable.

This phase is **required**, not optional — it's the primary differentiator from the subprocess approach.

1. Confirm from Phase 0.5 findings how ACP surfaces session metadata to clients.
2. If ACP's `session_new` accepts metadata and Desktop reads it: ensure the gateway tags correctly. Done.
3. If ACP accepts metadata but Desktop doesn't render it distinctly: open an issue upstream in `block/goose` with a concrete mockup and the ACP fields available. Document in `HANDOFF_REPORT.md`. Ship the gateway with correct metadata anyway; Desktop visibility improves once upstream lands.
4. If ACP lacks a metadata field: open an issue upstream proposing one. In the meantime, encode `[signal:+46700000001]` as a prefix on the session's display name or wherever user-visible naming is supported.
5. Write a smoke test doc section (`docs/desktop-integration.md`):
   - Start the gateway.
   - Send a Signal message to the bot.
   - Open Goose Desktop.
   - Screenshot / describe: the Signal session appears in the session list.
   - Click it. History shows the exchange.

This phase's deliverable is as much documentation and an upstream conversation as it is code.

**Findings (2026-04-18):** Sessions created by the gateway via `POST /agent/start` are fully functional in goosed — messages route, replies stream, token counts accumulate. However, **they do not appear in Goose Desktop's session sidebar.** Investigation showed:

- Session objects are structurally identical to Desktop-created sessions (`session_type: user`, same fields)
- goosed's `GET /sessions` returns them correctly
- The Desktop tracks sessions via `~/.local/share/goose/projects.json` (`last_session_id` per project path) — a local state file it manages itself
- The Desktop session list appears to reflect its own local state rather than polling goosed for all sessions

**Root cause:** Desktop session list does not react to externally-created sessions. It is a local view, not a live query against goosed.

**Recommended action:** File an upstream issue against `block/goose` requesting that the Desktop session list poll `GET /sessions` and surface externally-created sessions. The gateway already sets `session_type: user` and produces proper names (goosed auto-names sessions from the first message). No gateway-side change needed — this is purely a Desktop UX gap.

**Commit:** `phase 7b: session metadata`

### Phase 7c: Desktop → Signal forwarding (optional)

Goal: if the user types into a Signal-originated session from Goose Desktop, the reply is also sent to Signal.

Disabled by default (`forwarding.desktop_to_signal: false`). Implement but keep behind the flag.

Approach:

1. `Forwarder` runs a background task that subscribes to session events across all ACP sessions the gateway has created (via `session/load` on each, or whatever ACP's observer model supports per Phase 0.5).
2. For each assistant message observed:
   a. Check the session's metadata: is `source: signal`?
   b. If yes: was this assistant message produced in response to a prompt the gateway itself sent? If yes (we just saw this chunk stream), skip — the gateway already delivered it.
   c. If no (it was triggered by Desktop): forward the final assistant message to the Signal conversation.
3. Loop guard: maintain a short TTL cache of `{session_id, message_id}` pairs the gateway already handled. Entries expire after `forwarding.loop_guard_ttl_seconds`.
4. Also forward *user* messages typed in Desktop? Probably not — confusing UX, makes Signal feel like a broadcast. Skip for v1.

**Edge cases worth thinking through before coding:**

- Permission requests raised during a Desktop-typed prompt: should they route to Signal too? Probably no — the user is clearly at their computer. Scope: permission routing goes to whichever surface originated the prompt. The gateway needs to track this.
- Desktop typing happens mid-Signal-stream: the gateway is already streaming a Signal→agent reply; Desktop user butts in. ACP may or may not serialize this (depends on session semantics). Test behavior; don't assume.
- Multiple Desktop clients: two Desktops open on the same goosed both type into the same session — who wins? Out of scope; document as a known limitation.

**Tests:**

- Desktop-originated assistant message forwards to Signal.
- Gateway-originated assistant message does NOT double-forward.
- Loop guard: same message seen twice within TTL is only forwarded once.
- Forwarding disabled by config: no task runs.

**Commit:** `phase 7c: desktop-to-signal forwarding`

### Phase 8: CLI

Implement `cli.py`.

```
goose-signal setup                      Interactive first-run wizard.
goose-signal start                      Run gateway in foreground.
goose-signal start --detach             Install & start systemd --user unit.
goose-signal stop                       Stop systemd unit.
goose-signal status                     Is gateway running? Last event?
goose-signal doctor                     Diagnose health of every component.
goose-signal logs [-f]                  Tail gateway logs.
goose-signal pairing list
goose-signal pairing approve <code>
goose-signal pairing deny <code>
goose-signal pairing revoke <source>
goose-signal sessions                   List ACP sessions the gateway created.
goose-signal version
```

#### `goose-signal setup`

Uses `rich` and `click.prompt` / `click.confirm`.

1. **Check prerequisites.** `which signal-cli`, `which goose`, `which java`. Verify Python 3.12+. Abort with specific dnf commands per missing item:
   ```
   signal-cli not found. Install with:
       sudo dnf install signal-cli
   or see https://github.com/AsamK/signal-cli#installation
   ```
2. **ACP mode.** Ask: "How should the gateway connect to Goose?"
   - "Connect to an existing `goosed` (recommended if you use Goose Desktop)" → prompt for URL/port; try `initialize` to verify.
   - "Let the gateway manage its own `goosed`" → confirm binary path.
3. **Signal account.** Ask: "How should this gateway appear on Signal?"
   - "Link to my existing Signal account (recommended)" → linking flow.
   - "Register a new number" → pointer to signal-cli docs, don't implement.
   - "Already registered, just configure" → prompt for E.164.
4. **Linking flow** (if chosen).
   - Run `signal-cli link -n "goose-signal-gateway"`.
   - Capture the `tsdevice:/?uuid=...` URI.
   - Render as terminal QR via `qrcode.QRCode.print_ascii()`.
   - Instruct: "Signal → Settings → Linked devices → Link new device → scan."
   - Wait for link to complete; parse bot's own number.
5. **Daemon management.** "Should the gateway supervise signal-cli? (default no — use systemd.)"
6. **Access control.** Who can DM? (just me / specific list / pairing-only / anyone).
7. **Groups.** Enable? Require mention?
8. **Home conversation.** Where should startup notifications and scheduled task results go? Default: the user's own number.
9. **Write config.** Atomic write to `~/.config/goose-signal-gateway/config.yaml`.
10. **Test connection end-to-end:**
    - Ping signal-cli daemon.
    - Open SSE stream; wait for `stream_opened`.
    - Ping goosed; run ACP `initialize`.
    - Send Signal test message to `home_conversation`: "🎉 goose-signal-gateway is set up. Reply anything to talk to goose."
    - If Desktop is running and connected to the same goosed, remind user: "You should also see a new session in Goose Desktop titled 'Signal: +46…' — that's this conversation."
11. **Print next steps:**
    ```
    Setup complete.
    Start the gateway:
        goose-signal start                    (foreground)
        goose-signal start --detach           (systemd user unit)

    Verify health anytime:
        goose-signal doctor
    ```

Abort with a specific remediation hint on any failure. No silent fallbacks.

#### `goose-signal doctor`

Each check reports ✓ or ✗ with a specific fix suggestion:

1. Config file exists and parses.
2. `signal-cli` binary found.
3. `signal-cli` daemon reachable.
4. Java 21+ available.
5. signal-cli account matches `daemon.account` (via `get_account_details`).
6. Account is registered (not pending).
7. Signal SSE stream opens within 5 seconds.
8. `goose` and `goosed` binaries found.
9. goosed reachable at `acp.url`.
10. ACP `initialize` handshake succeeds.
11. Test session: create via `session_new`, delete — confirms write access.
12. Metadata round-trips (create with `source: signal`; read back).
13. systemd user unit status (if installed).
14. `home_conversation` present in `allowed_users`.

Exit nonzero if any failed. Summary: `N/14 checks passed.`

#### `goose-signal sessions`

Lists ACP sessions the gateway has created, with last-activity timestamp and Signal conversation. Useful for debugging and for seeing whether Desktop should be showing them.

```
Signal conversation          ACP session_id           Last activity
----------------------------  -----------------------  -------------------
dm:+46700000001               abc123-def456-...        2026-04-18 14:22:03
group:abcdef0123456789        xyz789-uvw012-...        2026-04-18 13:55:11
```

#### `goose-signal start --detach`

Copies `systemd/goose-signal-gateway.service` to `~/.config/systemd/user/`, runs `systemctl --user daemon-reload`, then `enable --now`. Verifies with `is-active` before returning.

**Tests:**

- `doctor` with good config: all pass.
- `doctor` with missing config: fails fast.
- `sessions` lists correctly from `SessionMap`.
- `setup` wizard end-to-end via `pexpect` against mock signal-cli + mock ACP.

**Commit:** `phase 8: cli`

### Phase 9: systemd units

`systemd/goose-signal-gateway.service` (user unit):

```ini
[Unit]
Description=Goose Signal Gateway
Documentation=https://github.com/<user>/goose-signal-gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/goose-signal start
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=%h/.config/goose-signal-gateway %h/.local/state/goose-signal-gateway
ProtectHome=false

[Install]
WantedBy=default.target
```

`systemd/signal-cli.service` (reference, not installed automatically — printed by doctor on failure):

```ini
[Unit]
Description=signal-cli daemon (HTTP mode)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/signal-cli --account %i daemon --http 127.0.0.1:8080
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
```

Instance name is the E.164 (escape `+` per systemd rules).

**Commit:** `phase 9: systemd units`

### Phase 10: Documentation

`README.md`:

1. What this is, in two sentences. Cross-link to the ACP design and to Goose.
2. **Prerequisites** — Fedora 42+, signal-cli, Java 21+, goose, goosed, Python 3.12+. Copy-paste install commands.
3. **Architecture** — a short version of the diagram above. Emphasize: the gateway is an ACP client. Sessions live in goosed. Desktop sees them.
4. **Install:** `uv tool install .` from the repo.
5. **Setup:** `goose-signal setup` walkthrough with the QR step described.
6. **Running with Goose Desktop.** A dedicated section: point both at the same goosed (document the URL and auth-token setup), open Desktop, see Signal sessions in the list. This is the hero feature.
7. **Running headless (server/VPS).** The other primary use case. Use `acp.manage_goosed: true` so the gateway owns a goosed it spawned.
8. **Run:** start, start --detach, stop, status.
9. **Troubleshooting** — a subsection per doctor check that can fail, with the exact fix.
10. **Security posture:**
    - Pairing is the default for a reason.
    - ACP permission prompts route to Signal.
    - Gateway has shell access via Goose; treat the config and systemd unit like admin credentials.
    - signal-cli stores keys in `~/.local/share/signal-cli`; sensitive directory.
    - Auth token for goosed is sensitive; file mode 0600.
11. **Known limitations:**
    - signal-cli version skew breaks things; pin in docs.
    - Edit-message streaming can hit rate limits on long responses.
    - Phase 7c's Desktop→Signal forwarding has edge cases around concurrent access.
    - Tool approval always asks Signal currently; Desktop-originated permissions should arguably stay Desktop-side (future work).
    - No stories, voice, video, or call answering.
12. **License:** MIT.

**Commit:** `phase 10: docs`

### Phase 11: End-to-end smoke test

On the user's machine with their real account linked:

1. Run `goose-signal setup` end-to-end.
2. DM the bot: "what's 2+2?" → streaming reply visible.
3. Open Goose Desktop. Confirm a session tagged `Signal: +46…` appears. Click it; see the exchange.
4. In Desktop, type a follow-up reply into that same session. (If Phase 7c enabled:) confirm it also arrives on Signal. (If 7c disabled:) confirm it only appears in Desktop.
5. From the phone, send: "run `ls ~` for me". Confirm Signal approval prompt arrives. Reply "yes". Command runs.
6. Restart the gateway. Send another Signal message in the same conversation. Confirm ACP `session_load` resumed cleanly and context is preserved.
7. Send from an un-allowlisted number; confirm pairing code.
8. `goose-signal pairing approve <code>`; confirm access.
9. Kill signal-cli daemon; confirm gateway reconnects on restart.
10. Kill goosed (if externally managed); confirm gateway reports cleanly (errors to Signal via `home_conversation`).
11. Long reply (~2 min of streaming); confirm no crashes, edits feel responsive.

Document any deltas from spec → real code. Fix them. Update `docs/acp-findings.md` if ACP behaved differently than Phase 0.5 assumed.

**Commit (only if needed):** `phase 11: smoke test fixes`

---

## Mock daemons (for development)

Both mocks are small FastAPI apps (dev-only dep), launched from `scripts/`.

**`scripts/dev-signal-mock.py`** — fake signal-cli:
- `POST /api/v1/rpc` — log call, return fake timestamp for `send`.
- `GET /api/v1/events` — SSE stream; reads from `/tmp/goose-signal-mock-events` named pipe and forwards as SSE.

**`scripts/dev-acp-mock.py`** — fake goosed:
- `POST /acp` — dispatch on method:
  - `initialize` → return capabilities stub.
  - `session/new` → return a UUID.
  - `session/prompt` → stream a scripted set of `agent_message_chunk` events followed by `session_complete`. Optionally inject a `permission_request` for testing Phase 6.
  - `session/load` → replay fake history.

Developer workflow:

```bash
# Terminal 1
python scripts/dev-signal-mock.py

# Terminal 2
python scripts/dev-acp-mock.py

# Terminal 3 — inject an inbound Signal message
echo '{"envelope":{"source":"+46700000001","dataMessage":{"message":"hello"},"timestamp":1700000000000}}' > /tmp/goose-signal-mock-events

# Terminal 4 — run the gateway
GOOSE_SIGNAL_CONFIG=~/work/test-config.yaml uv run goose-signal start
```

Every phase's tests run against these mocks, not real services.

---

## Explicit anti-goals (do NOT do these)

- **No subprocess fallback.** If goosed is unreachable at startup, fail with a clear error. No `goose run` fallback path.
- **No generic messaging abstraction.** One Signal adapter, concrete.
- **No native Signal protocol implementation.** Always go through `signal-cli`.
- **No message body caching on disk.** Only persistence is session map, pairing store, and logs.
- **No silent config-error downgrades.** Malformed config is a hard exit.
- **No retry on `UntrustedIdentityError`.** Human-in-the-loop.
- **No voice transcription / TTS / rich media in v1.**
- **No web dashboard.**
- **No Desktop UI changes from this repo.** If Desktop needs a fix to render session metadata properly, file an upstream issue and keep the gateway correct — don't patch Desktop from here.

---

## Handoff report

When all phases are done, produce `HANDOFF_REPORT.md`:

1. Phases completed.
2. What deviated from this spec and why (especially anything from Phase 0.5).
3. Open ACP questions or issues filed upstream.
4. Screenshot or description of Signal sessions appearing in Goose Desktop — this is the demo.
5. The one-liner to run: `cd goose-signal-gateway && uv tool install . && goose-signal setup`.
6. Known issues from smoke testing.
7. Next steps (e.g., Phase 7c if deferred, Desktop render work if filed upstream).

---

## Dialing in the first hour

Before committing any code:

1. Fetch the five reference URLs above. Summarize what you learned.
2. Run `goose --help`, `goosed --help`. Confirm the ACP endpoint path and default port.
3. Run `signal-cli --help`, `signal-cli daemon --help`. Confirm HTTP mode paths (`/api/v1/rpc`, `/api/v1/events` — correct if different).
4. `java -version`. Record for doctor check.
5. `python3 --version`.
6. `mkdir ~/code/goose-signal-gateway && cd $_ && uv init`. Begin Phase 0.
7. **After Phase 0, before Phase 1: execute Phase 0.5.** This is the single most important de-risking step. Do not skip.

Any discrepancy between this plan and reality is a **spec bug**. Fix code to match reality; note in `HANDOFF_REPORT.md`.

Good luck.
