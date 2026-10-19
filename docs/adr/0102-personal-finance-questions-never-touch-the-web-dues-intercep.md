# ADR-0102: Personal-finance questions never touch the web — dues intercept + research guard

> Source: section `### ADR-0102: Personal-finance questions never touch the web — dues intercept + research guard` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: A question about the user's OWN money — "any insurance dues?", "what are my
insurance payments", "what do I owe" — is answered deterministically from local data, never a
web search. Two layers: (1) a new `_handle_dues_request_turn` intercept (peer of the portfolio
intercept, gated by `IRIS_DUES_INTERCEPT`, default on) catches dues/insurance asks before the
ReAct loop and composes the local **dues digest** + a **targeted inbox search** (query-aware
FTS — insurance/EMI/card vocabularies, deduped by id and by subject+sender); (2) the `research`
tool itself refuses personal-finance queries (`_is_personal_finance_web_query`: a possessive
frame + a money noun, excluding informational asks) and now strips **any** email address from a
web query via `strip_emails`, not just identity-file identifiers.

**Rationale**: In a live Telegram session "Any insurance dues?" sent the finance ReAct agent to
the web `research` tool, which (a) put the user's email into the query, (b) fabricated "Tallman
Insurance Agency Inc." — a random BBB business — as the user's provider. Three harms: wrong tool
(web for personal finance), PII leak (a *secondary* Gmail account the identity files don't list,
so the name-only strip missed it), and fabrication. The web cannot know the user's accounts, so
the only correct source is local finance + inbox — which already held the user's real insurer and
policy notifications. Same pattern as the portfolio leak ([[project_portfolio_leak_fix]],
ADR-0092) and the deterministic-intercept lesson ([[project_finance_insights_and_networth_trend]]):
grounded + sensitive data → deterministic intercept beats LLM tool-choice. Both layers chosen
(defense-in-depth) at the user's request. Verified live: `dues_intercept=True`, no `@gmail`/name
leak, no Tallman, no web links.
