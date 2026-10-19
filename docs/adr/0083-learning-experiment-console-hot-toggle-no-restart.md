# ADR-0083: Learning experiment console (hot-toggle, no restart)

> Source: section `### ADR-0083: Learning experiment console (hot-toggle, no restart)` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: Make the digital-twin learning capabilities — behavior miner (L1),
intention rollup (L3), learning analyst — **flippable at runtime from the web**,
without an app reload, so the user can run them as experiments: toggle on, run, watch
the measurement, toggle off. The flags were read once at startup (`build_runtime`),
but the heartbeats gate only on whether the *component* exists
(`self.behavior_miner is None`, …), not on re-reading the env. So
`IrisRuntime.set_learning_flag(name, enabled)` rebuilds (enable) or nulls (disable)
the component live; `run_learning_now(name)` triggers a capability immediately instead
of waiting for its 12–24h heartbeat (the fast half of the loop); `learning_flags()`
reports each capability's live `enabled` plus the `env_default` a restart reverts to.
Overrides are **process-scoped** — a real restart reverts to the env gate, the right
default for an experiment. Surfaced at `GET /learning/flags` (open) and write-gated
`POST /learning/flags/{name}` + `/run` (`IRIS_WEBUI_ALLOW_WRITES`), with an Experiment
console panel on the web Learning screen: a toggle + Run-now per capability and the
live proposal-acceptance measurement beside them.

**Rationale**: The previous features made the loop *activatable and measurable* but
enabling still meant editing `.env` and restarting the stack — too slow and heavy for
"flip on, see what it proposes, flip off," which is how the user wants to tune it.
Hot-toggling turns enablement into a live control: the cost of trying a miner drops to
one click, and the measurement sits right next to the switch. The key enabler was
already there — the heartbeats gate on the component, not the env — so the change is a
thin runtime overlay, not a rewiring. Process-scoped (not persisted to `.env`) is
deliberate: an experiment should not silently change the durable config, and a restart
returning to a known-off state is a safety floor. Full design:
`docs/architecture/digital-twin.md` (experiment console) +
`docs/usage-guides/self-learning-and-experiments.md`; narrative
`docs/blog/2026-06-28-flip-the-switch-watch-it-learn.md`.

---
