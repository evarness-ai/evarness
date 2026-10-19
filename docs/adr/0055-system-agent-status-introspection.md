# ADR-0055: System agent — user-facing status + IRIS self-introspection

Status: Accepted (2026-06-14)
Context: System agent S0 — the 7th and last PAIA agent (PRD-03 §3.2). An audit found the *conversational* system surface already exists (the `system` ReAct handler: chat, time/date, weather, identity, web/wiki/memory search), and host pressure is already sampled — but only internally, for the LLM tier governor. The net-new, user-facing piece is a **status** surface.

## Decision

1. **A deterministic status surface composing existing signals.**
   `iris_harness.system.status` exposes `host_status()` (RAM free/total, CPU %, thermal
   throttle — reusing the tier governor's `sample_pressure`) and `iris_status()`
   (connected accounts by provider, skill count, enabled heartbeats, per-DB
   sizes, FileManager roots). Pure reads; no LLM, no OS mutation.

2. **Surfaced as a default-on `system-status` skill + `iris system status`.**
   The skill tool answers "how's your system / what's connected and running?";
   the CLI prints the same. (`iris status` already exists for *API health* —
   the new command is `iris system status`, a `system` sub-app, to avoid
   shadowing it.)

3. **No duplicate agent handlers.** The audit noted RAG/FileManager/Calendar
   have no discrete `agent_executor` handler — but that's by design (their
   skill tools are ReAct tools; the `system` handler fields them). The System
   agent does not add redundant handlers.

4. **OS-level control is out of scope (deferred).** Process/app launching,
   notifications (osascript), clipboard, settings — these mutate the host and
   deserve their own governance + justification (mission/heartbeat-driven).
   S0 is read-only introspection.

## Alternatives considered

- **Build a heavy "System agent" with OS control now.** Rejected — the
  high-value, low-risk capability is *visibility* (status/introspection); OS
  mutation is a separate, riskier surface that should be governed (like
  FileManager mutations) and demand-driven.
- **Expose the internal `ResourceGovernor` mode/hysteresis.** Kept minimal —
  report the raw snapshot (RAM/CPU/thermal); the governor's tier-downshift
  internals aren't user-facing concepts.
- **Discover skills via `SkillRegistry.discover()` for the count.** Rejected —
  that imports every skill's `tools.py` (heavy); counting `manifest.yaml`
  files on disk is cheap and sufficient for a status line.
- **Take the `status` command name.** Rejected — `iris status` is the existing
  API-health check; used `iris system status`.

## Consequences

**Positive:**
- A real, useful introspection surface: host load + what IRIS has connected
  (accounts), loaded (skills), scheduled (heartbeats), and stored (DB sizes) —
  composing signals that already existed but weren't visible.
- Deterministic + cheap; 5 tests (host metrics, full introspection with
  injected paths, empty case, combined report, skill load). Live-verified.
- Completes the 7 PAIA agents.

**Negative / deferred:**
- Read-only — no OS control (notifications, app/process launch, clipboard,
  settings); deferred until a governed, demand-driven need.
- `host_status` reads live psutil/pmset (macOS thermal); fast but not injected
  in the unit test (asserted loosely on types/ranges).
- IRIS self-status is filesystem/registry-derived, not a live runtime
  introspection of the in-process agent executor.

## Related ADRs

- ADR-0002 (tier governor / pressure sampling — reused), ADR-0016
  (`email_accounts` registry — account introspection), ADR-0051 (FileManager
  roots — introspected). PRD-03 §3.2 (System as the 7th PAIA agent).
