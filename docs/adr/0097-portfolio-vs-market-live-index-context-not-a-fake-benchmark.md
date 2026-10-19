# ADR-0097: Portfolio vs market — live index context, not a fake benchmark

> Source: section `### ADR-0097: Portfolio vs market — live index context, not a fake benchmark` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: "How is my portfolio doing against the market" appends **live market index
context** (Nifty 50 / Sensex / Bank Nifty, today's move) alongside the portfolio,
fetched locally via `fetch_indexes("IN")` — **never a web search**. `format_market_context`
(in `finance/portfolio.py`) renders the indexes with an explicit honest note, and the
portfolio tool fetches it only when the query mentions market/index/benchmark/vs/against.
It deliberately does **not** claim "you beat the market": a unit test forbids that
language.

**Rationale**: The data was verified first (the rule): only **20 of 57 positions are
intraday-priced — 2.4% of portfolio value** (the rest are mutual funds at last statement
NAV), and there's no historical index data locally. So a same-period return comparison
("you're up 39% vs the market's X%") is not honestly computable. Rather than fabricate one,
the feature gives what's true and useful: the market's current state next to the portfolio,
**locally** — which directly replaces the web-search-the-market behaviour that leaked the
user's name (issue 0031), with framing that says plainly why the index's *today* move and a
*lifetime* P&L aren't a like-for-like benchmark. A real over-time comparison is deferred to
accumulating index history (as net worth now does, ADR-0096). Narrative
`docs/blog/2026-06-29-the-market-next-to-you.md`.

---
