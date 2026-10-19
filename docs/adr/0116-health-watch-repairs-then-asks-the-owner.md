# ADR-0116: The health watch repairs what it can, then asks the owner — and remembers

Status: Accepted (2026-09-19)
Relates to: ADR-0069 (System Health), ADR-0030 (delivery-only channel routing), ADR-0108 (web channel), ADR-0073 (Action Center).
Amends: ADR-0069 §3 ("alerts are a projection, not a store") — the snapshot and the banner stay a projection; the *watch* keeps incidents.

## Context

ADR-0069 set out to "notice problems in the background — including expired/revoked
auth tokens — and tell the user". It shipped the noticing half: a 60-second snapshot of
services, credentials, hardware and heartbeat failures, rendered by `GET /health`, the
banner, the Action Center and the `system_health` tool. It never shipped the telling
half. Every surface is pull: a revoked Gmail token turned `email_sweep` red on a screen
nobody was looking at, and IRIS said nothing until the owner asked.

The owner's ask (2026-09-19): a health check for external connectivity (Gmail,
Calendar, any external system), a notice on the owner's channels when one breaks, and
a system that "makes sure it is up and running and fixes its issues — at the least
engages the user to fix the issue". Learning is not only about the owner's
conversations; IRIS should also learn how it fails.

Three things stood in the way:

1. Nothing pushed. The channel gateway could broadcast (reminders do), but health never
   called it.
2. Nothing repaired. Google's library refreshes an expired access token on use, and
   that was all. A dead governor stayed dead; a failed sweep waited for its next slot.
3. Nothing remembered. Pushing needs de-duplication (one notice, not one a minute),
   and de-duplication across a restart needs state. ADR-0069 §3 had rejected a stored
   alert as "a second source of truth".

## Decision

1. **A health watch runs after every `health_tick` refresh.** It walks each red check
   through one *incident*: **confirm** (red for `confirm_ticks` samples in a row — a
   service mid-restart is not an incident) → **repair** (up to `max_attempts`, one per
   tick, each judged by the *next snapshot*, never by the repairer's say-so) →
   **engage** (repairs ran out, or one reported that retrying cannot help: tell the
   owner) → **close** (the check is green or gone). Grey and yellow never open one.

2. **Incidents are stored** (`<data_dir>/health.db`, one row per stretch of red, keyed
   by `HealthCheck.key`): what was tried, when the owner was told and how often, and
   how it ended — `self_healed` (green before the owner was needed) or `user_fixed`
   (green after they were told). This is what stops a restart from re-alerting, what
   lets a notice say "the 3rd time in 7 days", and what makes a self-healed flap
   visible afterwards instead of vanishing. The snapshot, banner and `alerts()` stay a
   pure projection, so ADR-0069 §3's concern (two truths about *current* state) does
   not apply: incidents are history, not state.

3. **Every channel is told** (owner decision). One notice per incident through
   `ChannelGateway.broadcast` — Telegram, the web session (the shared `default` session,
   as heartbeat briefs are), console — with what was tried and the exact fix command. It
   is repeated only every `renotify_hours` (12 by default) while the check stays red, and
   followed by "working again" when it clears. Self-healed incidents are recorded but not
   announced (`notify.self_healed: false`). `notify.channels` can name a subset.

4. **Repairers are a keyed registry; the core ships only the repairs that need no
   external knowledge.** `register_repairer(key, fn)` sits beside
   `register_check_provider`: a repairer claims a red check or returns `None`.
   - Core: **re-run a failed heartbeat** once (never `health_tick` itself), and
     **restart a local service** with the command in `config/health_watch.yaml`
     (`scripts/start_iris.sh --restart=<name>`, `open -a Ollama`). The API process
     runs the watch and **never restarts itself** (`never_restart: [iris_api]`).
   - Plugins: the Gmail and Calendar plugins register a **forced token refresh**
     (`force_refresh`) through the generic `credential_refresh_repairer`. A refusal
     from Google (`invalid_grant`) is *final*: no retry can help, so the owner is asked
     at once. The core names no provider.

5. **Re-authentication stays with the owner and the governed CLI** (ADR-0069 §5
   unchanged). The watch refreshes tokens; it never starts an OAuth flow. Its notice
   carries `iris auth gmail login --user <address>`.

6. **One live credential probe per incident opened.** A failed sweep is most often a
   revoked token, and the local tick cannot tell revoked from healthy. When a pass opens
   an incident, the watch runs the ADR-0069 network probe once; a definitive "revoked"
   verdict is cached against that token and honoured by the local ticks that follow (a
   dict read, no egress), so the account turns red with its re-login command. A new
   token (the owner re-logged in) voids the old verdict. This is the only egress the
   watch adds, and `diagnose_credentials: false` turns it off.

7. **Every capability has an API and a CLI.** `GET /health/connectors[?live=true]`
   (the credential rows, each with its open incident; `live` exercises every token),
   `GET /health/incidents[?open_only=true&limit=]`, and `POST /health/watch` (one pass
   now; write-gated like every mutating route, since a pass can restart services). The
   CLI group `iris health connectors|incidents|watch` renders them. The new GET routes
   need the bearer token (they list account addresses), unlike the `/health` probe.

`HealthCheck` gains an optional `subject` (the account address on a credential row) so
one account's green row cannot close another account's incident. Kill-switch:
`IRIS_HEALTH_WATCH_ENABLED=0`.

## Alternatives considered

- **Keep incidents in memory only.** Keeps ADR-0069 §3 whole, but every restart
  re-sends every open alert, a self-healed failure leaves no trace, and "this keeps
  happening" cannot be said. Rejected by the owner.
- **Tell only the last-active channel, or only the default.** Needs presence tracking
  IRIS does not have (last-active), or misses an owner who is only in the web UI
  (default). The owner chose every channel; `notify.channels` narrows it.
- **Notify on the first red sample, repair later.** Turns every service restart and
  every transient network blip into a message. Confirm-then-repair-then-ask keeps the
  owner for what IRIS genuinely cannot fix.
- **Let the watch run `iris auth … login`.** An OAuth consent flow needs the owner's
  browser and consent; starting one from a background thread is the new auth surface
  ADR-0069 §5 rejected.
- **Put the Google refresh in the core.** It would make the core name a provider,
  against the plugin boundary (core = mechanisms, external apps = plugins).
- **Restart any service, including the API.** The process running the watch cannot
  restart itself and observe the result; a wedged API is for `start_iris.sh` or the owner.

## Consequences

**Positive:** a revoked token, a dead service or a failing sweep now reaches the owner
within about three minutes (two confirming ticks and the repair attempts), once, with
the fix; what IRIS can fix itself it fixes without bothering anyone; the incident log
is a record of how the system fails, which is the raw material for learning it.

**Negative:** a new store (`health.db`) and a second writer of channel messages; a
misconfigured restart command fails every tick until `max_attempts` runs out (then
the owner is told, which is the right outcome); `open -a Ollama` is macOS-only (on
Linux the launch fails and the owner is told — edit `health_watch.yaml`).

**Reversible:** `IRIS_HEALTH_WATCH_ENABLED=0` or `enabled: false` returns to ADR-0069
behaviour exactly; `health.db` can be deleted at any time (it only holds history).

## Related ADRs

ADR-0069 (the snapshot this watches — §3 amended), ADR-0030 (channel routing),
ADR-0108 (the web channel's in-chat delivery), ADR-0073 (health reds as Action Center
items — unchanged).
