# ADR-0002: Privacy-aware Tier 3 — local vs cloud resolved by per-category sensitivity

Status: Accepted (2026-05-24)
Context: Personal-assistant upgrade. Companion: `personal-assistant-upgrade.md` §4.2.

## Context

IRIS routes requests across three model tiers. Tier 3 is reserved for
the highest-capability model on the assumption that classification or
synthesis edge cases benefit from frontier-class reasoning. Cloud
providers (Anthropic, OpenAI, Google) are the obvious Tier 3 targets;
their cost per call is negligible for our volume.

The personal-assistant upgrade introduces email, finance, calendar, and
correlation skills that all want Tier 3 escalation for ambiguous cases.
The same skills will routinely handle highly sensitive personal data:
bank statements, family relationships, work-confidential threads,
account numbers, vendor patterns.

PII redaction (paia's mandatory approach) is necessary but not
sufficient. For classification, **pattern leakage** is the larger
threat: the *question* "Claude, is this a bank statement from Chase?"
reveals that the user banks with Chase, even when no account number is
present. Aggregated over weeks of triage, the cloud provider has a
profile of the user's institutions, vendors, family contacts, and
subscriptions.

## Decision

Tier 3 has **two flavors** resolved per request:

- `tier3.cloud` — frontier cloud model (e.g., Claude Haiku, GPT-4o-mini,
  Gemini Flash). Sanitized payloads only.
- `tier3.local` — large local model via Ollama (e.g., Qwen3 MoE family,
  ~3B activated params). `egress: none`.

Resolution is driven by a **sensitivity attribute per category**
declared in YAML config (`categories.yaml` for email; analogous configs
for finance, etc.):

```yaml
categories:
  - id: finance-statement
    sensitivity: high          # → tier3.local only
  - id: newsletter
    sensitivity: low           # → tier3.cloud OK
```

Default sensitivity is `high` (privacy-first). The governance kernel
enforces the routing rule; skill code cannot opt itself into cloud.

## Alternatives considered

1. **Cloud-only Tier 3 with hardened PII redaction.** Rejected because
   redaction does not solve pattern leakage, only content leakage.
2. **Local-only Tier 3.** Wastes capacity on low-sensitivity categories
   that would benefit from the lowest-cost cloud calls; forces the user
   to own a 32GB+ Mac for everything including newsletter classification.
3. **One Tier 3 per skill.** Couples privacy posture to skill
   identity; same skill operating on a high-sensitivity email category
   vs a low-sensitivity one would need separate routes. Awkward.
4. **Per-message sensitivity classifier.** Adds a separate ML model
   to decide privacy. Rejected: the kNN classification step already
   produces a candidate category before Tier 3 escalation; the category
   carries sensitivity for free. No additional model needed.

## Consequences

**Positive:**
- By default, no email or finance content reaches a cloud LLM.
- Users with weak hardware can flip more categories to `low` and accept
  the privacy cost knowingly. Users in stricter environments (medical,
  legal) can set every category to `high` and the cloud path is dead
  code at their site. Same code, same skills.
- Egress audit is one place: every cloud call carries
  `route=email/classify`, `category=...`, `payload_class=features-only`.

**Negative:**
- Requires a local-large model running via Ollama. Pre-install step:
  `ollama pull <tier3.local model tag>`. First-run setup cost.
- Battery / thermal load on the laptop when local Tier 3 fires. Mitigated
  by heartbeat condition `on_ac_power: true` for battery-sensitive
  routines; default off (don't gate by default).
- ~5–10% accuracy loss vs frontier cloud on truly ambiguous cases.
  Acceptable trade.

**Reversibility:** high. Sensitivity is a YAML field; flipping a single
category from `high` to `low` is a config change. The architecture
supports both paths.
