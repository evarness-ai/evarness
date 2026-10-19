# ADR-0114: Memory is what the owner confirmed, and context is what the prompt actually carries

Status: Accepted (2026-09-17)
Relates to: ADR-0004 (wiki as cross-domain correlation — superseded in part), ADR-0010 (wiki entity precision), ADR-0025 (email → wiki), ADR-0077 (context budgets), ADR-0079 (window-aware compaction), ADR-0081/0084 (context health, compact now), ADR-0082 (learn before you forget), ADR-0105 (fact-store coherence), ADR-0086 (self-management tools).
Supersedes: ADR-0004's automatic ingest and the wiki's role as the agent's knowledge layer.
Amended by: ADR-0115 (decision 7 — the graph stays a view, over statements instead of `key=value` facts).

## Context

The owner asked a plain question: *are you really using this wiki, and if not, how do we
make what IRIS gathers useful?* The answer, measured rather than assumed:

- `data/wiki/log.md` held **2,243 `ingest` lines and 0 `query` lines** across four months.
  Session logs show **0 `wiki_search` calls**. The only reader was an optional tool that
  competes for a slot in a 12-tool shortlist, and losing that competition means the model
  never learns the tool exists.
- What ingest produced: **2,505 entity pages, 2,175 typed `person`** — including "Account
  Ending", "Actually Use" and "Abu Dhabi" — 64% single-source, **zero `[[wikilinks]]`**.
  Every chat turn and every classified email wrote one, in-line, before the reply
  returned.
- Meanwhile the agent could not see its own conversation. The prompt rendered
  `recent_turns[-3:]`; `MemoryContext.summary` had **no writer at all**; cross-session
  recall was fetched every turn with **no relevance cutoff** and then cut by the same
  three-line tail; USER.md was capped at 2,000 characters against a 3,444-character
  curated profile.
- What it "knew" about the user was mined without review: **58 facts**, including
  `name=ollama` (confidence 1.00), `employer=Department of Justice` (0.90, from a pasted
  news article), `location=IRIS`, `topic=murder plot`.
- What it "knew" about itself was hand-written and wrong: SOUL.md named `llama3.2:3b` and
  `qwen2.5-coder:7b` as tiers 1 and 2 while the box ran `granite4` and `qwen2.5:7b-instruct`.
- Its "lessons" were four rows of the form *"the finance agent answered it cleanly"*,
  fetched every turn and dropped unless an opt-in flag was set, which it never was.

The common thread: **producers were well tested and wired to nothing.** Six features had
shipped that way before; memory was the largest instance.

## Decision

**1. Automatic wiki ingest is off.** `IRIS_WIKI_INGEST` gates the chat-turn feed, the
`email.classified` bridge and the startup re-index. Reads still work. The pages and their
vectors were deleted with the owner's confirmation. The wiki may return as an optional
plugin; it is not the agent's knowledge layer.

**2. Context is three budgeted layers.**
- **L0** — always, in full: SOUL core, the curated head of USER.md, top-N confirmed
  facts, active items, the rolling summary, and the recent conversation **filled to a
  token budget** rather than a fixed tail.
- **L1** — always, one line each: what memory exists but is *not* in this prompt, naming
  the tool that fetches it.
- **L2** — on demand: `memory_search(scope=…)` and `recall_conversation`.

Small local models rarely call a tool unprompted (0 `wiki_search`, 2 `memory_search` calls
in four months of logs), so L0 stays generous and L1 nudges rather than relying on the
model's initiative.

**3. Nothing the agent mines becomes a belief without the owner — but "the owner" does
not have to mean "a queue".** Extraction proposes; confidence never confirms (the store
held `name=ollama` at 1.00). Two filters run ahead of the validation gates: a YAML key
allowlist and a first-person gate (quoted and fenced text stripped). Recall **and the
semantic index** carry confirmed facts only.

Amended 2026-09-17 after the owner pointed out the obvious: a queue-only design means
IRIS learns nothing until someone does homework, which is the opposite of the point.
Confirmation is tiered by how plainly the fact was stated — **A** a plain self-statement
is confirmed during the turn and announced in the reply with a three-word undo; **B** an
inferred fact costs one question per conversation, asked in the conversation; **C**
everything else queues. Repetition across three turns also confirms. Tier A cannot admit
`name=ollama`: the message carries the value but not the shape.

**4. A lesson needs evidence.** A user correction, a tool failure the run recovered from,
or an evaluator halt — not "it went fine". An approved lesson is written as a behavior
file, reusing the injection path that already exists rather than adding a second one.

**5. Self-knowledge is generated, not written.** The capability line and
`iris_doc("CAPABILITIES")` read the live registry and tier router. SOUL.md is split at
read time by config: identity and hard rules stay in every prompt; the internals tour,
style notes and full tool policy move behind `iris_doc("OPERATING")`.

**6. Retention keeps a summary, never nothing.** Conversations cool to their summary after
90 days — but **only if they have one**; a session that was never summarized keeps its
text. Playground, eval and test runs are not persisted at all. One seam deletes SQLite
rows and their vectors together.

**7. The graph is a view.** Computed per request from confirmed facts, summaries,
behaviors and patterns; no store, no extractor, opens on "You" and expands by click.
Forget a fact and the node is gone on the next read.

## Consequences

Measured on the owner's machine:

| | Before | After |
|---|---|---|
| Wiki writes / reads | 2,243 / 0 | 0 / 0 |
| Conversation lines in the prompt | 3 | 12 (budget-filled) |
| Session summary | never rendered; extractive | rendered, sectioned, rolled forward |
| SOUL.md per turn | 1,851 tokens | 887 |
| Facts reaching the prompt | 58 unreviewed | 0 until approved |
| Stored sessions | 1,029 (620 were test runs) | 408 real |

Costs accepted:

- **Tier A trades a little precision for learning that actually happens.** A plain
  self-statement is believed without being asked about; the undo is one phrase and the
  reply always says what was learned, so a wrong one is visible immediately rather than
  discovered months later in an audit.
- **Tier B costs an interruption**, capped at one per conversation.
- **The 58 legacy facts still want review.** They are inert until then — but new learning
  no longer waits on them.
- **Capture is narrower on purpose.** The allowlist and first-person gate will miss real
  facts stated obliquely. Precision was the problem; recall can be widened from evidence.
- **One junk case survives the capture gates by design.** `name=ollama` from "i am running
  ollama for local models" is an allowed key, a first-person sentence, and the value is in
  the message. No gate can separate it from a real name — confirmation is the answer, and
  the corpus keeps the case rather than tuning it away.
- **Summaries cost a local LLM call**, off the reply path, one roll per session at a time.
- **Lesson capture is one source, not three.** Corrections are wired; tool-recovery and
  evaluator-halt are not.

## Verification

- Deterministic gate: `tests/integration/test_memory_continuity.py` — the chain on one
  runtime, asserting on the prompt.
- Live: `iris playground run memory-continuity`, 5/5 on `granite4:latest` and on
  `qwen2.5:7b-instruct`.
- Capture precision: `iris facts gate-eval` over a corpus carrying the real junk.
- `GET /memory/context/{session}` shows every block, its tokens, and what is **absent** —
  the standing check against shipping wired to nothing.
