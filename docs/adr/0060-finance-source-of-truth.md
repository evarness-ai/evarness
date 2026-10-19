# ADR-0060: Finance source-of-truth — authority, identity-keyed dedup, bounded agent reasoning

Status: Accepted (2026-06-17)
Context: Finance metrics (net worth, balances, monthly transactions) are computed from **overlapping** document sources. A depository CAS subsumes a registrar MF CAS; contract notes are trades not positions; margin reports aren't balance-bearing. Today overlap is resolved by hand (deactivating the superseded account, ADR — see #120/#121). This ADR makes source selection deterministic, auditable, and automatic, with a bounded agent layer that validates and explains. All examples below use **synthetic** values (no real account data — ADR privacy rule).

## Context

A user's money appears in several documents at once, and each metric wants a
different authoritative source:

- **Depository CAS** (e.g. NSDL/CDSL consolidated): every demat equity + MF
  folio across all accounts — the superset for **investments / net worth**.
- **Registrar MF CAS** (e.g. CAMS/KFintech): a mutual-fund-only **subset**
  already inside the depository CAS — counting both double-counts.
- **Bank statement**: one account's ledger + closing balance — authoritative
  for that account's **cash balance** and its **transactions**. A CAS carries
  *no* bank balances.
- **Card statement**: card ledger + dues — **transactions** + **liabilities**.
- **Contract note**: a day's individual *trades* (buys/sells) — trade history,
  **not** positions, **not** net worth.
- **Margin report / advice / promo**: not a statement — must be **ignored**
  (it polluted balances when mis-ingested).

Naively summing everything double-counts (the live bug: a registrar MF folio
counted both standalone and inside the depository CAS). The decision of "which
source is authoritative for which metric, and how to drop overlap" has five
dimensions: **role** (what a doc contributes), **authority** (who wins on
overlap), **coverage/identity** (a CAS spans many accounts; a bank statement
one), **recency** (latest snapshot; transactions accumulate), and **quality**
(deterministic+reconciled > LLM+unreconciled; junk rejected).

## Decision

Adopt a **hybrid** model: a deterministic resolver (declarative authority +
identity-keyed dedup) computes the numbers, and a **bounded agent layer**
validates and surfaces the decision. The agent reasons *within* the rules —
it never invents sources or overrides figures (consistent with ADR-0035:
numbers come from code, not the model; here, *source selection* comes from
rules, not the model).

### 1. Document role map (doc-type → contribution)

Each statement `document_type` maps to exactly one role:

| Role | Doc types | Feeds |
|---|---|---|
| `holdings` | depository CAS, registrar CAS, broker holding statement | investments |
| `cash` | bank statement, wallet statement | cash balance |
| `liability` | card statement, loan statement | liabilities |
| `transactions` | bank, card (ledgers) | monthly transactions |
| `trade_ledger` | contract note | trade history only (informational) |
| `ignore` | margin report, advice, promo, unknown | nothing |

This alone fixes margin-reports/contract-notes being mistaken for
balance-bearing statements: they never feed balances or net worth.

### 2. Declarative source authority (config)

A per-source `authority_rank` (higher wins) lives in config (alongside the
institution registry). Example ranks for the `holdings` role:

```
depository_cas      : 100   # NSDL/CDSL consolidated — canonical for investments
registrar_cas       :  60   # CAMS/KFintech MF-only — subset of depository
broker_holding_stmt :  40   # single-broker positions
```

For `cash`/`liability`, a bank/card account's **own** statement is
authoritative for *that account* (rank by recency, not cross-source).

### 3. Identity-keyed dedup (the robust core)

Normalise every contribution to a **canonical identity**, then keep the value
from the highest-authority, most-recent source per identity:

- **Holdings** → key on **ISIN** (equities/bonds) or **folio** (MFs); fall back
  to `symbol`/normalised-name when absent.
- **Cash/liability** → key on **account identity**.

Net worth = sum over *deduped* keys. This handles **partial** overlap
automatically: a depository CAS covering folios A+B and a separate US broker
covering holding C → union by identity (A,B from the CAS, C from the broker),
no double-count, no manual deactivation. Two Indian sources both listing
folio A → keep the depository CAS's value (rank 100 > 60).

> Phased: per-security identity requires per-security extraction of the CAS
> (today we extract the asset-class summary). Until then, dedup is at the
> **source level** — "a depository CAS, when present, is the sole `holdings`
> source for its jurisdiction; lower-rank holding sources are suppressed" —
> which reproduces today's manual reconciliation automatically.

### 4. Net-worth formula (user decision)

Per currency, never FX-summed (ADR-0007):

```
investments = Σ deduped holdings           # GROSS — never reduced by liabilities
liquid      = bank_cash − liabilities      # liabilities net ONLY against cash
net_worth   = investments + liquid
```

**Investments are reported gross** (the canonical CAS value). Liabilities
(card dues, loans) subtract from the **bank/cash** side only — they never
reduce the investments figure.

**Presentation (user decision): liabilities are shown as their OWN line**, not
pre-netted into the cash figure. The breakdown lists investments / cash /
liabilities as distinct lines; the total nets them:

```
Net worth (INR):  <total = investments + cash − liabilities>
  Investments:    <gross>
  Cash:           <bank balance>
  Liabilities:   −<card dues + loans>
```

`cash − liabilities` may go negative (debt exceeds cash); investments stand on
their own line, untouched.

### 5. Per-metric resolution

- **Net worth** — canonical depository CAS for all investments (gross) +
  per-account bank cash − liabilities. Latest snapshot per source.
- **Balances** — latest statement *per account, from that account's own
  statement* (a CAS does not override a bank's balance).
- **Monthly transactions** — union of `transactions`-role ledgers (bank +
  card), deduped by transaction identity, accumulated across periods. CAS
  (a snapshot) and contract notes (a different ledger) are excluded.

### 6. Bounded agent layer (validate + reason + surface)

The deterministic resolver emits the figures **and** a `SourceDecision`
record (which sources were used / suppressed / ignored, and why). The agent
then, *within the rules*:

- **Validates** — reconciliation/sanity checks (e.g. asset-class subtotal vs.
  the CAS's stated consolidated total; flags a mismatch rather than trusting
  blindly).
- **Reasons** — interprets in-rule ambiguity (e.g. notes a gap like no bank
  statement for a known account).
- **Surfaces** — explains the decision in the answer (transparency): *"Net
  worth from the Apr-2026 depository CAS; bank cash from 2 statements; a
  registrar CAS and a margin report were excluded."*

**Conflict resolution is deterministic (user decision Q4): IRIS decides
conflicts by the rules automatically — it does NOT gate on user confirmation.**
The agent *notifies* (surfaces what it decided and why) but does not ask
permission; overlap/precedence is resolved by `authority_rank` + recency, gaps
are flagged for visibility, and the manual override (§7) remains for the rare
case the user wants to change the outcome. The agent **cannot** change a figure
or choose a source outside the declared authority — it explains; the
deterministic engine decides.

### 7. Manual override (escape hatch)

`deactivate_account` (and the active-account filtering from #120/#121) remains
as a manual override for cases the rules don't yet cover.

## Consequences

- **Correct, auditable net worth** without per-statement hand-tuning; overlap
  resolves from rules + identity, not vibes.
- **Junk is structurally excluded** (margin reports, promos) via the role map.
- **Extensible** — new institutions/doc-types get a role + rank in config.
- **Cost**: needs identity extraction (ISIN/folio) for full dedup; until then,
  source-level supersession covers the common case.
- **Trust preserved** — money figures stay deterministic; the LLM only
  explains/validates, never selects or computes.

## Build plan

**SOT-0 — role map + ignore junk** *(small, immediate)*
`document_role(doc_type)` map; mark margin-reports/advices/promos `ignore` at
ingest; exclude `ignore`/`trade_ledger` from balance/holdings/net-worth queries
(complements the active-account filter). Backfill existing rows. Tests:
margin-report contributes nothing; contract note feeds trade history only.

**SOT-1 — declarative authority + source-level supersession** *(the automation
of today's manual deactivation)*
`config/finance/source_authority.yaml` (doc-type/issuer → role + rank). A
`net_worth` resolver that, for the `holdings` role, keeps the highest-rank
source per jurisdiction and suppresses subordinates — so a depository CAS
auto-supersedes a registrar CAS without deactivating accounts. Emit the
`SourceDecision` record. Tests with synthetic multi-source fixtures.

**SOT-2 — identity-keyed dedup** *(depends on per-security CAS extraction)*
Extend CAS extraction to per-security rows (ISIN/folio, qty, value). Dedup
holdings by identity across sources; enable partial-overlap + cross-jurisdiction
union. Tests: overlapping folios dedup to the authoritative value; disjoint
holdings union.

**SOT-3 — liabilities + full net-worth formula**
Card/loan `liability` role wired in; net worth = investments(gross) +
(bank_cash − liabilities), per currency. CLI/chat digest shows liabilities as a
**separate line** (investments / cash / liabilities / total) + the
`SourceDecision` summary.

**SOT-4 — bounded agent validation + surfacing (notify, don't gate)**
Wire the agent layer: validate (reconcile subtotals), reason (flag in-rule
ambiguity/gaps), and **surface** the source decision in the chat answer.
Conflicts are resolved deterministically by the engine; the agent only
*notifies* what it decided (no confirmation gate). Governance hooks fire on the
(read-only) reasoning call.

**SOT-5 — monthly transactions resolver**
Union bank+card ledgers, dedup by transaction identity, accumulate across
periods; exclude snapshots/contract-notes. Power `iris finance spend` /
transaction views from the resolver.
