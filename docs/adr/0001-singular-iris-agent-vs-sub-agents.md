# ADR-0001: Singular IRIS agent, skill packs only — no sub-agents

Status: Accepted (2026-05-24)
Context: Personal-assistant upgrade. Companion: `docs/architecture/personal-assistant-upgrade.md` §2, §3.

## Context

The personal-assistant upgrade adds email, calendar, finance, tasks,
reminders, bills, and cross-domain synthesis to IRIS. The user has prior
experience with paia, a sibling project that adopted a multi-agent
architecture: `BaseAgent(ABC)` subclasses (EmailAgent, FinanceAgent,
CalendarAgent, FilemanagerAgent, ...) coordinated by a 3,038-line
`orchestrator.py`. Paia was abandoned; one stated reason was the
orchestration tax.

The natural pull when designing the upgrade is to recreate that shape
inside IRIS: an "email agent" that owns Gmail, reasoning, and approval
flow; a "finance agent" that owns statements and ledger; etc. The
question is whether IRIS's architecture should accommodate sub-agents
alongside (or under) the harness.

## Decision

**IRIS has exactly one agent: the Harness.** It is the ReAct loop driven
by the 3-tier LLM router. Every other capability is a **skill** (manifest
+ tools) optionally driven by **routines** (scheduled invocations) or
**heartbeats** (scheduled I/O). The word "agent" is reserved for the
harness in IRIS documentation and code.

The word "skill pack" is a *folder grouping convention* for related
skills (`config/skills/email/`), not a runtime class.

## Alternatives considered

1. **paia-style sub-agents.** Each domain (email, finance, calendar) gets
   a `BaseAgent` subclass with its own command vocabulary and reasoning
   loop. The harness becomes an orchestrator dispatching across them.
   Rejected: this is the shape the user already abandoned. It produces
   the orchestrator-class bloat and the per-agent micro-architecture tax
   (each grows its own templates, audit helpers, error handling).

2. **Skills + a thin "agent" wrapper.** A `SkillPackage` class that
   bundles skills and exposes a higher-level façade. Rejected: a noun
   without a verb. What does the wrapper *do* that a folder + a governor
   route prefix doesn't? Nothing operational. Adds schema migrations
   without value.

3. **One agent + pluggable "reasoning modules" per domain.** Sub-models
   loaded by topic. Rejected: this is what the 3-tier router already
   does at the request level; replicating it at the domain level
   competes with the router.

## Consequences

**Positive:**
- Open-source contributors ship a skill by dropping a folder; they do
  not subclass an agent.
- The orchestration debt that killed paia does not re-accrue.
- Inter-domain coordination becomes a routine that calls multiple skills
  in sequence — declared in YAML, not buried in Python.
- The word "agent" remains singular and meaningful. `AGENTS.md` (in the
  identity layer) describes *other* agents IRIS collaborates with, not
  sub-agents inside it.

**Negative:**
- Any capability that genuinely needs an autonomous, long-running
  reasoning loop (e.g., a continuous "finance watcher" reasoning over a
  week of transactions) must be modeled as a routine + skill, which is
  syntactically heavier than a daemon class would be. Acceptable: no
  such capability is on the roadmap.
- Contributors arriving with "agentic framework" mental models from
  LangGraph/CrewAI/Autogen will need to be re-oriented in docs. One
  paragraph in the contributor guide handles it.

**Reversibility:** moderate. Reintroducing sub-agents later would
require a runtime concept that does not exist today. It is a
one-direction door for now; if a future capability demands it, the
re-derivation is non-trivial.
