# ADR-0059: Planner brief "today's plan" headline via a synthesis slot

Status: Accepted (2026-06-14)
Context: Planner P2 (ADR-0046) attempted a morning-brief "today's plan" headline but **dropped** it: the `todays_plan` slot's text ("Today's plan / Nothing scheduled today") scored ≥ threshold against a bare *"send me a morning briefing"* request in `match_brief_slots`, so routine-authoring auto-bound that one section instead of asking the user which sections they wanted (regressing the clarify UX). This ADR re-adds the headline correctly.

## Decision

1. **A `synthesis` flag on `BriefToolSlot`.** A synthesis slot is a
   headline that *composes* other sections (the daily plan composes events /
   tasks / followups / bills). It **renders** in the brief like any slot, but
   is excluded from `brief_tool_slot_keys` — so it is **not a user-selectable
   section** and **not scored** in `match_brief_slots`.

2. **The `todays_plan` slot is `synthesis: true`.** Re-added at the top of the
   morning-brief layout, driven by the planner `plan_today` tool
   (deterministic bullets). Because it's excluded from section matching, a bare
   "morning briefing" request still matches *no* sections → the authoring flow
   asks which sections (clarify), exactly as before. When the brief renders
   (default or a section-scoped routine), the headline appears.

## Alternatives considered

- **Special-case `todays_plan` by name in `match_brief_slots`.** Rejected —
  brittle; a declarative `synthesis` flag generalises to any future headline
  slot.
- **Lower the match threshold / tune wording so it doesn't collide.** Rejected
  — fragile against embedding drift; the real fix is "headlines aren't
  selectable sections".
- **Leave the headline dropped (P2 state).** Superseded — the unified plan is
  the most useful line of the brief; it just shouldn't be a *section*.
- **Exclude only in `match_brief_slots`, not `brief_tool_slot_keys`.**
  Rejected — excluding at the key source also (correctly) removes it from
  section *selection* + capability resolution, not just scoring.

## Consequences

**Positive:**
- The brief leads with the prioritized "today's plan" headline again, with no
  regression to the clarify UX (the dropped-in-P2 feature lands safely).
- `synthesis` is a reusable, declarative concept for any composing headline.
- The previously-regressing `test_parse_generic_morning_briefing_clarifies_sections`
  passes; new tests assert the slot renders yet is excluded from selectable keys.

**Negative / deferred:**
- The brief headline is the *deterministic* plan bullets (the P2-style LLM
  narrative stays on the interactive "plan my day" turn, not the brief).
- `synthesis` slots are all-or-nothing (render + non-selectable); no partial
  "selectable but not auto-matched" mode (not needed today).

## Related ADRs

- ADR-0044/0045/0046 (Planner P0–P2 — P2 dropped this headline; now restored),
  ADR-0029 (brief slots).
