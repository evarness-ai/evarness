# ADR-0049: RAG grounded cited Q&A + tag scope + MCP exposure

Status: Accepted (2026-06-14). **MCP exposure superseded (2026-09-30):** `iris mcp serve` no longer
serves document search (or any skill) by default. Every served call is governed, only contained reads are
served without `--tool`/`--skill`, and personal results are withheld from clients the owner has not declared
local (`config/governance/mcp-serve.yaml`). The grounded Q&A decisions below stand.
Context: RAG agent R3 (final RAG slice). Builds on ADR-0047 (document foundation) + ADR-0048 (Obsidian connector). Turns retrieval-of-chunks into a NotebookLM-style answering surface, and exposes document search to external MCP clients.

## Decision

1. **Grounded cited answers — retrieval picks the facts, the LLM only
   summarises them.** `iris_harness.rag.qa.answer_question` retrieves the top chunks
   (R0/R1), then asks the LLM to answer using **only** those numbered sources
   and to cite them inline `[n]`; a `Sources:` list maps `[n]` → citation. The
   facts come from the user's documents, never the model's parametric memory —
   the same deterministic-first contract as Finance/Calendar/Planner. The LLM
   cannot invent sources because the citations are produced by retrieval.

2. **Always grounded, even with no model.** With no `llm_call` (or on
   error / empty output) `answer_question` returns the cited **passages**
   themselves (`grounded=False`) rather than nothing — an answer is always
   attributable. The CLI builds a best-effort local Tier-1 `llm_call`
   (`default_llm_call`, the standalone `CodingLLMClient` pattern Finance uses)
   and degrades to passages when no local model is configured. Synthesis is
   local — documents never leave the box.

3. **Tag scope = a lightweight "notebook".** `search_documents(..., tags=…)`
   restricts retrieval to sources carrying any of those tags (over-fetch then
   post-filter by source tags, cached). `iris docs ask "…" --tag finance`
   answers from just that slice. A full named-notebook subsystem is deferred;
   tags (already captured from Obsidian front-matter in R1) are the cheap,
   useful scope.

4. **Expose document search over MCP.** The stdlib MCP server (built for
   Calendar C3) is generalised: `SERVER_NAME` `iris-calendar` → `iris`,
   `DEFAULT_SKILLS` now `("calendar-events", "docs-search")`. `iris mcp serve`
   exposes both; tools still run through their `BaseTool` interface so their
   governance holds. External clients (Claude Desktop, other agents) can now
   query the user's indexed knowledge.

5. **No separate chat handler hijack.** The `docs-search` skill is already a
   ReAct tool (R0), so the system handler can ground+cite from documents in
   chat without a dedicated `rag` agent — and `search`/`research` queries keep
   going to web/wiki search, not the user's private docs.

## Alternatives considered

- **A dedicated `rag` agent handler for "search" intent.** Rejected — would
  divert web-search/research queries to the user's documents. Document Q&A is
  a *tool* (and the `iris docs ask` CLI), not the meaning of "search".
- **LLM answers from its own knowledge, documents as hints.** Rejected —
  breaks grounding; the prompt forbids outside knowledge and the deterministic
  fallback returns only retrieved passages.
- **Full named-notebook subsystem (collections table, membership).** Deferred —
  tag scope covers "ask only my finance notes" with zero new schema; named
  notebooks can layer on later.
- **Heavier MCP server / per-skill servers.** Rejected — one general `iris`
  server exposing a default skill set, extensible via `--skill`, is simpler.

## Consequences

**Positive:**
- "Ask my documents" returns a grounded, cited answer (or the cited passages
  when no model) — the NotebookLM experience, fully local.
- Tag scope gives notebook-like filtering for free off R1's front-matter tags.
- IRIS's document knowledge is now queryable by any MCP client.
- The RAG agent is feature-complete for markdown/Obsidian (R0→R1→R3); PDFs
  (R2) slot into the same pipeline when needed.

**Negative / deferred:**
- Named notebooks (vs tags), backlink/graph traversal, and live file-watching
  remain future work.
- The CLI synthesis is a single local Tier-1 call (no multi-hop / re-ranking).
- PDF & other formats (R2) are still unbuilt; ask/search cover indexed
  markdown/text today.

## Related ADRs

- ADR-0047 (document foundation), ADR-0048 (Obsidian connector — front-matter
  tags power the scope), ADR-0043 (the stdlib MCP server generalised here),
  ADR-0035 (deterministic-first — retrieval grounds, LLM summarises).
