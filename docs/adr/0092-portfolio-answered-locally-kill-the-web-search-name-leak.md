# ADR-0092: Portfolio answered locally — kill the web-search name leak

> Source: section `### ADR-0092: Portfolio answered locally — kill the web-search name leak` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: Two fixes so portfolio questions are answered from the user's OWN data and
never leak their name to the web (issue 0031). **(1) A local `portfolio` tool.**
`_domain_tools` exposes a `portfolio` tool that calls `build_portfolio()` and renders a
deterministic `format_portfolio_summary` (total value, P&L vs cost, top holdings, biggest
gainers/losers); its description directs the model to it — NEVER `research` — for "show my
portfolio" / "how is my portfolio doing" / "net worth" / "top profits", and it's reserved
so a skill can't shadow it. **(2) A research privacy guard.** New pure
`research/privacy.py`: `extract_personal_identifiers(user_md, soul_md)` **derives** the
identifiers from the identity files (names from USER.md — never SOUL.md's *agent* name;
emails from either), and `strip_owner_identifiers(query, ids)` removes them + any
possessive from a research query before egress; `_research` applies it and logs the strip.
The identifiers are not hardcoded — they track whatever the profile records (per the user:
"the leak should be from my SOUL.md or USER.md file").

**Rationale**: A live session showed the finance agent calling `research` with the user's
name in the query ("latest market performance of <name>'s portfolio") — web junk instead
of holdings, and a personal-data egress in a privacy-first system. Root cause was twofold:
the agent had **no local portfolio tool** (so it reached for the only relevant-looking
tool, the always-on `research`), and **the query carried the user's name**. The fix
addresses both — the tool removes the *reason* to web-search, and the guard is
defense-in-depth so even a future stray prompt can't leak the name. Both are deterministic,
grounded in local data, and unit-tested. Verified live (no personal content): the strip
turns "<name>'s portfolio" → "portfolio"; the tool returns the real holdings from local
data with no http/web. Issue write-up
`docs/issues/0031-portfolio-web-search-name-leak.md`; narrative
`docs/blog/2026-06-29-your-portfolio-stays-home.md`.

---
