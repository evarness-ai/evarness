# ADR-0103: Action requests escalate to a capable model (generic, not finance-specific)

> Source: section `### ADR-0103: Action requests escalate to a capable model (generic, not finance-specific)` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision (Phase 1 of a 4-layer design)**: An imperative "do something" turn ("Can you update my
daily briefing…", "remind me when I get this email", "create a notification when…") is recognised
as an **action** and reasoned on a capable model, not the weak tier-1 executor. Three pieces:
- **Layer 0 — read-intercepts never swallow an action.** A shared `is_action_request()`
  (`intent_router.py`) keys on MUTATE verbs (update/add/configure/remind/create/enable/…), not on
  polite framing. Every deterministic read-shortcut — dues, portfolio, brief — bails when it fires,
  so an action reaches classification + escalation instead of a list dump.
- **Layer 1 — a first-class `action` intent + predictive promotion.** After classification,
  `promote_to_action_intent()` relabels a turn that landed on a weak tier-1 intent
  (general/system/help) to `action` (agent = system, full tool set) when `is_action_request` holds.
  Domain actions already on a capable tier (finance/calendar/communication/files/planner/coding) are
  left untouched. Applied on BOTH `chat()` and `chat_stream()`.
- **Layer 2 — `action` → tier2** (`qwen2.5:7b-instruct`) in `llm_tiers.yaml`; the escalation judge
  (`_maybe_escalate`) can bump to tier3 if tier2 still struggles.

Generic by construction — the model decides which tools are available and takes a governance-gated
safe action; the briefing was merely the first concrete case.

**Layer 3 (shipped, on-demand path)**: the agent can now actually *fulfil* a brief-config action.
A per-user brief-prefs overlay (`src/iris_harness/runtime/handlers/brief_prefs.py`, JSON under the data dir;
default = full brief, so unchanged until configured) is read by the on-demand brief render and
written by a new `configure_brief` ReAct tool (add/remove/show sections, resolves user terms →
manifest slots, reversible direct write like `memory_correct`). The promotion was extended: a
*config-surface* action ("add the dues section to my **briefing**") routes to `action`/system even
when a domain noun ("dues") would pull it to the finance agent that lacks the configure tools
(`_CONFIG_OBJECT_RE`). Verified live on `/chat/stream`: natural phrasing → `intent=action` → the
system agent calls `configure_brief` → the prefs persist (all sections kept, the requested one
surfaced first) → "show my brief" renders accordingly. Deferred: the scheduled-routine render
reading these prefs (needs data-dir threading + authored-vs-global merge), the dues
window+category granularity ("next 7 days, insurance/rent/card" — dues carry no category
dimension today), turning the escalation judge to enforce (Layer 2 reactive), and the residual
tier-2 reliability gap (the local 7B occasionally confabulates success instead of calling the
tool — the tier-3 escalation is the intended safety net).

**Reliability postscript (measured, the big lesson)**: the tier-3 "safety net" above was tested and
**failed** — and the failure is the lesson. `configure_brief` tool-call rate on natural phrasing:
**tier-2 (qwen2.5:7b) ≈ 25%**, escalation judge in enforce → **0 escalations** (it judged the
confabulated "done" responses `accept`, conf 0.9–0.95 — a quality judge can't catch a *good-looking
lie*), and **tier-3 (qwen3.6-35b) = 0%** — the bigger model went straight to a final answer with no
tool call *every* time. **A larger model does not fix "the model chose not to call the tool"; it made
it worse.** Resolution: a deterministic `_handle_brief_config_turn` intercept (`IRIS_BRIEF_CONFIG_INTERCEPT`,
default on) parses "add/remove X to/from my brief" + window + categories and calls the
`configure_brief` logic directly, bypassing the LLM and the relevance shortlist entirely →
**100% (5/5)**. Same pattern as the dues/portfolio intercepts ([[project_finance_insights_and_networth_trend]]):
for a grounded, parseable action, a deterministic intercept beats LLM tool-choice — and beats
throwing tiers at it. Narrative: `docs/blog/2026-06-30-the-tool-the-model-wouldnt-call.md`.

**Rationale**: "Can you update my daily briefing to add the outstanding dues…" is an action, but it
(a) got swallowed by the dues read-intercept (a deterministic short-circuit that fired on "dues"
before any reasoning), and (b) even un-hijacked, an imperative with no domain home falls to
general/system → tier1 `granite4`, the weak executor — so multi-step actions reasoned and
tool-called poorly. The IRIS escalation engine already existed (`_maybe_escalate`, ADR-0068) but is
reactive + off-by-default; raising the **start tier** for actions is predictive and cheaper. Built
as a deterministic post-classification promotion (not a new keyword rule competing in the 240-line
ordered list) to avoid regressing existing routing. Verified live on `/chat/stream` (the REPL path):
the dues action is no longer hijacked and routes to tier2; a generic "set my timezone to PST" →
`intent=action` → tier2. Follows [[feedback_test_all_paths_interfaces]] (gated all three intercepts;
wired both chat paths) and [[feedback_agentic_over_heuristic]].
