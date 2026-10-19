# ADR-0085: Measure the memory garbage-in / garbage-out guarantees

> Source: section `### ADR-0085: Measure the memory garbage-in / garbage-out guarantees` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: Add a **measurement harness** for the two defences that keep the user
profile clean — the **capture gates** (`fact_validation.py`: plausibility, durability,
grounding) that decide what enters the store, and the **recall filter**
(`retriever._filter_facts`: `min_fact_confidence` drop + `uncertain_below` mark) that
decides what reaches the prompt. Both shipped with thresholds; nothing *measured*
whether they were right. `iris_harness.memory.gate_audit` scores them on **labelled**
candidates: `audit_capture_gates` → precision/recall + **false-admit** (junk that
slipped) + **false-reject** (real facts over-blocked) with per-gate attribution;
`audit_recall_filter` → **false-recall** (junk that reached the prompt) + **false-drop**;
`sweep_recall_threshold` → a balanced-accuracy threshold suggestion. `run_full_audit`
models the real pipeline (gates on all candidates; recall on the *admitted* subset, so
junk the gates already blocked can't inflate the recall-leak count). A built-in
synthetic corpus (`gate_audit_corpus.DEFAULT_CORPUS`) exercises each gate's reject path
*and* the defence-in-depth seam; `iris facts gate-eval [--corpus x.json]` renders it and
accepts custom labelled data.

**Rationale**: "keep garbage out" was an unmeasured article of faith spread across two
subsystems — you couldn't say *how much* junk slips the gates, *how much* reaches the
prompt, or whether a threshold was too tight or too loose. The harness turns that into
numbers and, crucially, **distinguishes the two failure directions** (false-admit /
false-recall = leaks; false-reject / false-drop = over-blocking a real fact) so a tuning
move trades them deliberately instead of blindly. It deliberately **measures and
suggests but does not auto-tune**: the sweep's suggestion is evidence *on the given
corpus*, and changing the production `IRIS_MEMORY_MIN_FACT_CONFIDENCE` stays a human,
real-data decision (consistent with the "validate before you enable" posture). Measured
on the default corpus it already surfaces three honest signals: a 33% gate false-admit
rate (low-confidence pollution passes the structural gates — caught downstream by the
recall filter, which is *why* defence-in-depth exists), one recall leak at the current
0.35 threshold, and one grounding over-block (the conservative grounding gate rejects a
`nyc → New York City` normalization, a known, intentional trade). Full design:
`docs/architecture/memory-subsystem.md` (audit harness); narrative
`docs/blog/2026-06-28-measuring-the-garbage-filter.md`.

---
