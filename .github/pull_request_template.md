<!-- What does this change, and why? Link the issue if one exists. -->

## Checklist

- [ ] `pytest` passes; `ruff`, `black`, `mypy` clean (CI runs all four at pinned versions)
- [ ] **Digest-neutral** — the golden-digest tests pass unchanged. If canonical output must change, that is a versioned `c1` bump with its own decision-log entry, never a re-pinned value.
- [ ] Core imports nothing from `domains`/`io`/`cli` (the boundary test enforces this)
- [ ] Design decision? Add a row to `DECISIONS.md` — the log is part of the product
- [ ] Docs updated where behavior changed (`mkdocs build --strict` must stay clean)
