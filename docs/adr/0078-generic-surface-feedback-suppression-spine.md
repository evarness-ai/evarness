# ADR-0078: Generic surface-feedback + suppression spine

**Status pass (2026-10-18):** the decision holds; the code now lives in `services/learning/suppression.py`, `harness_api/session_routes.py`, `runtime/react_tools.py`. the design doc it references, `surface-feedback-suppression.md`, was never written

> Source: section `### ADR-0078: Generic surface-feedback + suppression spine` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: Add one subsystem-agnostic learning spine so that whenever IRIS
**proactively surfaces** an item — an email reply-follow-up, a finance bill or
spend anomaly, a system-health alert, a portfolio mover, a low-trust research
source — the user can mark it *not useful* and IRIS **suppresses similar items**
next time. The store (`src/iris_harness/learning/suppression.py`, `learning.db`) keys
feedback on `(subsystem, surface_kind, dims)` where `dims` are stable identifying
dimensions (email: `account · from_domain`; finance: `label · currency`; system:
`kind · target`; …). `verdict ∈ {not_useful, useful}`; suppression fires when
`#not_useful − #useful ≥ min_fp` (default 1 for explicit user feedback), so a
later "useful" lifts it. Each surfaced item carries a self-describing `fb:` token;
feedback is given via `iris feedback <task_id|fb:ref>`, `POST /surface-feedback`,
the `record_feedback` chat tool, and a **"not useful" button on the web Action
Center** (the thin-renderer rule — one store behind every channel, never UI-only).
Every verdict also mirrors into the digital-twin Layer-2 behavior signals.

It is **standalone, not riding the ADR-0073 Task/PendingAction seam**, because the
proactive surfaces are heterogeneous (Tasks, read-time projections, brief items, a
rank signal) and a Task-only spine would not reach health/research/portfolio. All
five proactive subsystems are wired per-consult-point: email follow-ups (creation
gate), finance bills + spend anomalies, system-health alerts, portfolio brief
highlight lists (movers/best/worst, never the totals), and research source ranking
(downrank, not drop — research is pull, never proactive). Suppression acts only on
signals the user explicitly gave, so it is safe **on by default**.

Alongside, two deterministic follow-up guards (no learning needed): skip
provider bulk-tab labels (`CATEGORY_{PROMOTIONS,SOCIAL,UPDATES,FORUMS}`) and skip
mail confidently filed under a **non-actionable category root** (allowlist:
`personal, work, finance, jobs, travel, community`; env
`IRIS_FOLLOWUP_ACTIONABLE_ROOTS`). Full design:
`docs/architecture/surface-feedback-suppression.md`; narrative
`docs/blog/2026-06-27-teaching-iris-to-take-a-hint.md`.

**Rationale**: A live "how is my day looking" surfaced a Quant Academy promotional
blast as the day's top priority — "Reply to … Can AI build you a profitable
trading bot?" — because the follow-up detector judged the mail blind
(`category_path=None`, `email.labels` ignored) and the topical classifier had
filed marketing under `email/learning/...`. The personalized "Rahul, …" greeting
made "calls my name" a false positive. The deterministic guards stop *this* class
at the source; the feedback spine generalizes the user's request ("flag this as
part of your learning; let me tell you it's a false positive") into a capability
every proactive subsystem shares rather than an email one-off. See issue 0028.

---
