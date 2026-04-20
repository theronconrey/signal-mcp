# Notes from building an external ACP consumer

**Audience:** goose maintainers, ACP contributors at Block / AAIF.
**Author:** Theron. hollerback builder / user — a Python Signal gateway that drives goosed via ACP.
**Intent:** Share three concrete gaps I hit as an outside builder targeting ACP, so the protocol and its goose implementation are stronger when more non-IDE clients arrive. Not a feature ticket for my project. Each section below articulates the gap, explains what a single third-party consumer found, with some thoughts around a shape for the fix — with the explicit caveat that this is just my thoughts here, not a proposal.

---

## Framing

Discussion [#7697](https://github.com/aaif-goose/goose/discussions/7697) notes that goose's ACP implementation already uses custom methods for things the standard protocol doesn't cover, and that maintainers are open to promoting useful extensions upstream to the ACP spec. This document is written in that spirit — three gaps that matter for any non-IDE ACP consumer (messaging gateways, notification agents, mobile clients, background automations), not just for the specific thing I'm building.

IDE-first ACP clients (Zed, JetBrains, VS Code) don't hit these gaps because they own the session UI entirely and don't share it with anyone. Anything that drives goose *alongside* Desktop does. The existing `goose gateway telegram` implementation works around these same gaps internally — having them exposed in the public protocol would let that implementation get simpler, and let external builders converge on the same shape.

Each gap below was genuine friction, not speculation. Where goose Desktop works around the gap internally with privileged access to `goose-server` state, external ACP consumers can't do the same thing, so the behavior visibly degrades.

---

## Gap 1: Sessions created over ACP are invisible to Desktop

**Symptom.** When an external ACP client creates a session via `session/new`, Desktop does not know the session exists. Opening Desktop after sessions have been created via ACP shows a sidebar that omits those sessions entirely; restarting Desktop picks them up from storage, but live creation is not reflected.

**Why it matters for non-IDE clients.** A Telegram bot session, a Signal gateway session, a cron-scheduled recipe session, a mobile-app session — any agent surface that runs alongside Desktop creates sessions the user has legitimate reasons to see in their primary UI. Today the user has to close Desktop and reopen it, which is jarring when the user is actively reading the Signal conversation on their phone and has Desktop open on their laptop for context. This would apply identically to a WhatsApp, iMessage, Matrix, or email gateway.

**What would be awesome.** ACP already defines session-lifecycle methods (`session/new`, `session/load`) and per-session `SessionNotification` variants for in-session streaming (chunks, tool calls). What it doesn't define is an out-of-band notification channel for *session-level* events — a stream Desktop or any observer could subscribe to that says "a session was created," "a session was updated," "a session was closed." IDE clients don't need this because they own session creation. Multi-client deployments do. User switches from Signal to Desktop and wants to keep working scenario.

**Possible shapes.**

*Option A: New ACP method `sessions/subscribe`.* A client calls it once, receives a stream of `SessionEvent` notifications (`Created`, `Updated`, `Closed`) with the session ID and summary metadata. Desktop subscribes on startup and stays in sync. External clients can subscribe too if they care.

*Option B: Session events flow on a shared notification channel alongside `SessionNotification`.* Same substantively but avoids adding a new subscribe method. The per-session notification system gets a wildcard or session-agnostic flavor.

My assumption is that Option B is probably closer to how goose's internal event bus already works.

**Prior work to check.** The session persistence layer already fires internal events when sessions are created (it has to, for SQLite persistence). Wiring those into an ACP-visible notification should be mostly a transport question, not a new state-tracking question.

**Why I don't think this is a Desktop hack.** One possible response is "just have external clients tell Desktop directly." That's the wrong coupling — it asks each external client to know about Desktop, which defeats the point of having a protocol. The clean shape is that goose-server announces session events on ACP, and any subscriber (Desktop, a monitoring dashboard, a mobile app, hollerback) listens.

---

## Gap 2: No way to attach display metadata to a session at creation

**Symptom.** `session/new` takes a client ID (for working-directory and filesystem context) but doesn't accept a display name, label, or tags. The only thing Desktop has to show in its sidebar is the session ID (a UUID) or whatever it can derive from the session's first message. For an ACP-driven session whose context is "this is my conversation with Alice on Signal," there's no way to tell goose "label this 'Signal: Alice (+1-612-555-1234)'" so Desktop shows something useful.

**Why it matters for non-IDE clients.** Everyone loves sessions with meaningful names. Without this, Desktop's sidebar fills up with UUIDs and auto-generated first-message summaries that don't reflect the session's actual provenance.

`goose gateway telegram` already handles this internally — Desktop's Gateways panel says "See which session each user is connected to," which requires per-session metadata that's not exposed in the public ACP surface. External clients can't reach the same affordance.

**What's missing.** ACP's `session/new` could accept an optional `metadata` field — a small structured object with `display_name`, optional `tags`, and maybe a `source` identifier for the creating client. This metadata could be attached to the session at creation and visible to any subsequent `session/get` or `sessions/subscribe` event.

**Possible shape.** Extending the existing `session/new` request with an optional field is the minimally invasive path:

```json
{
  "method": "session/new",
  "params": {
    "clientId": "hollerback",
    "metadata": {
      "displayName": "Signal: +16125551234",
      "tags": ["signal", "external-gateway"],
      "source": "hollerback/0.1"
    }
  }
}
```

Three notes on the shape:

- **Optional, backward-compatible.** Existing clients not passing metadata get today's behavior. IDE clients probably don't pass it; gateway clients do.
- **`displayName` is the minimum useful field.** `tags` and `source` are nice-to-haves; the single most valuable thing is a human-readable label.
- **ACP-spec-worthy.** This is the kind of thing Discussion #7697 describes as potentially upstreamable — other agents serving non-IDE clients will want the same field. Worth proposing to the ACP spec directly if it lands in goose first.

**Harder version of this problem.** If the metadata should be *updatable* after session creation (a session gets renamed as its content becomes clearer), that's either a new `session/setMetadata` method or metadata changes flow as `SessionUpdate` variants.

---

## Gap 3: Tool-approval resolution has no external-client story

**Symptom.** goose's permission system for tool execution is visible to ACP clients for in-session tool calls (`ToolCall` notifications with `status: pending`). What's unclear is how an external approver — not the client that initiated the turn — responds to approval requests.

**Why it matters for non-IDE clients.** Consider a Signal user messaging goose. Goose decides to run a shell command. The approval prompt needs to land in Signal (where the human is), not in Desktop (where the human isn't). The human replies "yes" or "no" in Signal. That reply needs to get back to goose as an approval decision — but the Signal gateway is not the ACP client that's waiting on the approval; the agent-turn loop is. There's no public ACP method for "resolve a pending approval identified by ID" that an out-of-band client can call.

Same issue applies to:
- A mobile notification asking for approval while you're away from your laptop.
- A Slack bot where approvals land in a channel and get clicked by a teammate.
- A scheduled recipe that paused on an approval and wants a response when a human finally looks at a dashboard.

**What's missing.** An ACP method — call it `approvals/resolve` — that takes an approval ID and a decision (`approve` / `deny` / `approve_always` / `deny_always`) and completes the pending tool call. This needs to be callable by any ACP-authorized client, not just the one that started the turn.

goose's internal implementation (for Telegram) already does this — Telegram's inline approval buttons have to somehow route back into the agent loop to resolve a pending ToolCall. The plumbing exists; what's missing is exposing it on ACP so external clients can hit the same code path.

**Suggested shape.**

```json
{
  "method": "approvals/resolve",
  "params": {
    "approvalId": "appr_abc123...",
    "decision": "approve",
    "rationale": "Signal user +16125551234 replied 'yes' at 2026-04-20T14:02:33Z"
  }
}
```

The `approvalId` is whatever goose already generates internally and surfaces in the `ToolCall` notification's `approval` field (or similar). The `rationale` is optional but useful for audit.

**Authorization question.** Not every ACP client should be able to resolve any approval — that's a security regression. A sensible default: an approval can only be resolved by a client that's authorized against the same session, or that holds a gateway-level credential granting `approvals:resolve` permission. This is a real design question, not rhetorical — worth thinking about before exposing the method publicly.

---

## What makes these three tractable

All three gaps live at the ACP-method layer, which means:

- **They don't require restructuring goose.** The internal implementations mostly exist already (Telegram Gateway uses them); what's missing is the public protocol surface.
- **They're plausibly upstream-to-ACP-spec worthy.** Every gap here affects any agent serving non-IDE clients, not just goose. Discussion #7697 describes exactly this class of extension as the kind goose might bring to the ACP spec.
- **They don't commit goose to a position on gateway extensibility.** Whether `goose gateway` stays internal-only or opens to external channels, these protocol gaps still matter — because external ACP clients other than gateways (mobile apps, monitoring dashboards, cron-scheduled recipes) also need them.

I'd love to see ACP as the unified interface, `goose serve` as the deployment model, "community UIs as the third category", this is the sort of small, well-scoped ACP extensions that make that direction concretely useful to outside builders.

---

## What I'm not asking for

Being explicit, because it's easy to read requests as bigger than they are:

- **Not asking for a feature in hollerback.** hollerback works around all three gaps today with the usual hacks (no live Desktop visibility, UUID sessions, approval flow wired but inactive).
- **Not asking for a decision on `goose gateway` extensibility.** Different question, different thread.
- **Not asking for priority.** These could sit in the backlog for months without hurting hollerback. They'd help anyone building something similar, including future goose gateway authors (internal or external).
- **Not asking for my specific shape.** The JSON examples above are illustrative. A maintainer who designs this area every day will have better instincts on transport details, namespace, auth model.

---

## How I'd be happy to contribute

If any of these land as accepted-direction issues, I can:

- Prototype the Rust implementation in `goose-acp` if the maintainers want a starting point to review rather than write from scratch. Happy to be told "no thanks, we'll do this ourselves" — offering the option, not claiming the ticket.
- Test against hollerback end-to-end as an external consumer, which catches the "works in the Telegram Gateway but doesn't generalize" class of regression.

Primary intent of this document is to make the gaps legible, not to stake out the work. I would like to help.

---

## Not my lane

Whether these three are best as one combined "ACP gaps for non-IDE clients" discussion, three separate issues, or folded into an existing ACP roadmap doc. Each has merits:

- **Combined discussion:** preserves the "these are related gaps for a class of client" framing.
- **Three issues:** each is tractable and trackable as a separate unit of work.
- **Folded into #7697 or #7309:** lets existing protocol design conversations absorb them naturally.

If there's a preferred place to start the conversation, I'll go there.

---

*Written by someone who built something against ACP and wants the protocol to get better. Shared ahead of filing so the framing can be critiqued before it enters the issue tracker.*
