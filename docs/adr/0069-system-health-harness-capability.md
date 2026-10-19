# ADR-0069: System Health — a harness capability with local-on / network-opt-in probing

Status: Accepted (2026-06-20) — implemented in slices 1-5 on `feat/system-health-slice-1`

**Status pass (2026-10-18):** the decision holds; the code now lives in `evarness/services/health/`, `sdk/health.py`, `harness_api/health_routes.py`. decision 3 (alerts are not stored) is amended by ADR-0116, which adds incidents and repairs

Amended by: ADR-0116 (2026-09-19) — §3: the snapshot stays a projection, but the health watch now keeps incidents, repairs, and notifies the owner on every channel.
Context: Today IRIS only learns a credential died when something *uses* it (e.g.
`email_sweep` fails with `invalid_grant`). ADR-0055 shipped *passive* status
introspection (`iris_harness.system.status` — host load, connected-account counts, skill
count, DB sizes) but explicitly deferred live runtime introspection, and it does
not check credential *health* (expiry/revocation), probe service reachability,
or raise anything to the user's attention. The ask is a runtime health surface
that **notices problems in the background** — including expired/revoked auth
tokens — and tells the user. The governing constraint (from the design review):
this must be a **harness capability, not a Web UI feature**, and it must stay
minimal.

## Decision

1. **Health is one harness capability, surfaced through existing machinery.**
   A new `iris_harness.health` package builds a `HealthSnapshot` from `HealthCheck`s over
   three target classes: **services** (the 6 IRIS services' `/healthz` + Ollama
   `/api/tags`), **credentials** (Gmail/Calendar/Drive/Copilot + cloud-LLM keys),
   and **hardware** (reusing `iris_harness.system.status.host_status` — no re-sampling).
   It is exposed as a `system_health` ReAct ToolSpec (so chat, CLI, and any
   channel can ask "is anything broken?"), observed by a `health_tick` heartbeat,
   and read over `GET /health`. The Web UI Health screen + banner are *thin
   renderers* over `/health` — no health logic lives in React.

2. **Four states; not-configured is neutral.** A check is green / yellow / red /
   **grey (not configured)**. Only a *configured* target can degrade to
   yellow/red and draw attention; a never-connected integration is grey and
   informational. This removes the "you haven't set up Drive" nag without hiding
   the option, and removes any need for a per-alert mute mechanism.

3. **Alerts are a projection, not a store.** An "Alert" is `snapshot.checks`
   filtered to *red AND carrying a remediation action* — computed on read, never
   persisted. There is no alert table, ack, or snooze: an alert lives exactly as
   long as its red check and clears when the next probe goes green. The Web UI
   banner shows only this set (red + actionable); everything else is a row on the
   Health screen.

4. **Local checks on by default; network probing opt-in.** Cheap, zero-egress
   checks (service ports, credential expiry read from the stored blob, hardware,
   learning-health, and ingestion of failures from heartbeats that already run
   like `email_sweep`) run by default. The only piece that makes *new* outbound
   calls — an hourly live refresh-probe to detect *revoked* (not merely expired)
   idle credentials — is gated behind `IRIS_HEALTH_NET_PROBE=1`. The whole
   subsystem can be disabled with `IRIS_HEALTH_ENABLED=0`.

5. **Re-authentication reuses the governed command path — no new auth surface.**
   A red credential check's remediation is the exact existing CLI
   (`iris auth gmail login …`). The user (or the Web UI banner button) asks the
   agent, which runs that command under the normal governance/approval path.
   IRIS does **not** add a bespoke OAuth-triggering HTTP endpoint on a surface
   whose own gate is "a kill-switch, not auth" (ADR/PR #206).

## Alternatives considered

- **A dedicated dashboard built in the Web UI.** Rejected — would strand the
  capability behind one channel. The agent itself must be able to report its
  health; the UI is one renderer.
- **A persisted alert store with ack/snooze/mute (the initial sketch).**
  Rejected as over-engineering for a single-user local assistant: a second
  source of truth to reconcile with the snapshot. The grey state already kills
  the only real nag, so alerts collapse to a pure projection.
- **One-click server-side OAuth trigger (`POST /auth/{provider}/login`).**
  Rejected — a new auth-initiating HTTP surface crosses the trust boundary of an
  unauthenticated-by-default Web UI. Reusing the governed CLI path is both safer
  and harness-native.
- **Active network probing on by default / for everything.** Rejected against the
  privacy-first, egress-governed posture. Local checks + opportunistic ingestion
  of existing sweep failures already catch the real (actively-used) case; the
  network probe only adds idle-credential coverage and so is opt-in.
- **Fold service/credential health into the existing `system-status` skill.**
  Kept distinct: `system-status` answers *"what's connected/loaded"* (inventory,
  ADR-0055); `system_health` answers *"what's broken / needs attention"*
  (wellness + alerts). Health *composes* `host_status`/account introspection
  rather than re-implementing them.

## Consequences

**Positive:**
- The 2 a.m. token death is caught and surfaced without anyone opening a screen;
  the agent can volunteer it in chat and offer the one-command fix.
- Minimal blast radius: one package, one tool, one heartbeat, one endpoint, one
  thin screen + banner. Hardware and account signals are reused from ADR-0055.
- Naming the endpoint `/health` revives the existing `iris status` CLI (it
  already calls `GET /health`; iris_api previously served only `/healthz`).
- Privacy posture preserved: default behavior adds no new egress.

**Negative / deferred:**
- With the network probe off (default), a *revoked-but-unexpired* credential for
  an *idle* integration stays invisible until used or until the user opts in.
  Even with the probe on, revocation is non-invasive: `load_credentials` only
  refreshes when the access token is already expired, so a revoked-but-unexpired
  token surfaces on its next expiry (~1h), not the instant it is revoked. Forcing
  a refresh was rejected — it rotates refresh tokens (testing-mode apps rotate
  them ~weekly) for marginal latency gain.
- No alert history/audit (projection-only) — a flap that self-heals leaves no
  durable trace beyond the existing session/audit logs.
- The intelligence panel (learning patterns/accuracy/recommender, item #4) and
  the runtime-update checker (item #5) are explicitly **out of scope** here;
  they become fast-follows that reuse this renderer.

## Related ADRs

- ADR-0055 (passive system status/introspection — *extended* here from passive to
  active + credential-aware). ADR-0002 (tier governor pressure sampling — reused
  for hardware). ADR-0003 (Keychain credentials — the targets of the credential
  checks). PR #204 (revoked Google token no longer crashes callers — the failure
  this surfaces). PR #206 (Web UI write gate — why re-auth reuses the governed
  path instead of a new endpoint).
