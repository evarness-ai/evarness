# ADR-0139: A grant is the owner's remembered answer to a card, and relaxes approval only

Status: Accepted (2026-11-01), not implemented. Work package 3 of the Evarness Next
specification (`docs/architecture/evarness-next-spec.md`, section 5).
Relates to: `kernel/governance/approvals/schema.sql` (`approval_queue`, the card),
`kernel/governance/plugins/destructive_approval.py` (ADR-0118), the kernel table
`grants` (new), `foundation/capabilities.py` (`ToolEffect`, `EgressDataClass`).

## Context

Approval today is per call (ADR-0118): a destructive tool call raises a card, the owner
answers, the call runs or not. A bot that reads the same mailbox every morning raises the
same card every morning. The owner will stop reading cards, which is the failure the
cards exist to prevent. Something has to remember an answer, and whatever remembers it
must not become a second policy engine that the gate, classification and locality can
be argued around.

## Decision

A card has five answers (decision D9): `once`, `task`, `always`, `deny_remember`,
`not_now`. `task` and `always` write a grant; `deny_remember` writes a `deny` grant with
the same scope choice; `once` and `not_now` write nothing (spec 5.1).

A grant is a kernel row (spec 5.2): one `bot_id`, never a wildcard; a `subject_kind` of
`tool`, `capability` or `message` and its `subject`; an `effect` of `read` or `write`;
a flat scope of literal matchers (spec 5.3: `host`, `path_prefix`, `mailbox`, `label`,
`target_bot`, `resource`, `arg:<name>`; no regular expressions, no wildcard but a leading
`*.` on `host`); a `data_class_max`; `allow` or `deny`; the `approval_id` of the card it
answers; and `expires_at`, required for every row and at most thirty days out (decision
D12).

Matching is deterministic (spec 5.4): load the bot's active, unexpired grants for the
subject; deny wins; an allow matches when every scope key matches exactly and the call's
data class is at or below the ceiling; no match means ask.

A grant relaxes approval only (decision D10). The kernel enforces:

- G1. No grant has `effect = destructive`. The schema check and the hook both refuse it.
- G2. No grant is consulted for a bot other than its `bot_id`.
- G3. A grant never allows a model call or an egress that the gate refuses. The gate runs
  first.
- G4. On expiry the next matching call raises one re-confirmation card; the answer writes
  a new row and the old one stays for the ledger.
- G5. The model never reads the grants table. It receives `allowed` or `ask`.
- G6. A learning proposal writes no grant. It writes a card, and the owner's answer writes
  the grant with `granted_by = owner_via_proposal` (decision D11; the learning job itself
  is the platform's, with the thresholds in spec 5.5).

## Alternatives

- A policy file per bot that lists what is pre-approved. The owner would be writing
  policy instead of answering cards, and the file would need the same matcher language
  with none of the ledger.
- Grants with patterns or wildcards in scope. A pattern the owner did not foresee is an
  approval the owner did not give.
- Grants for destructive effects with a short TTL. The whole value of per-call approval
  for a destructive action is that it is per call (ADR-0118).
- A longer TTL for `always`. Thirty days is the longest the owner can be expected to
  remember what they answered.

## Consequences

- Every grant traces to a card: `approval_id` is required, so the ledger can show the
  owner's answer behind any write that ran without a fresh card (spec section 11).
- A `task` grant expires when its run reaches a terminal state, so the workflow store
  (ADR-0141) closes grants as part of finishing a run.
- A `message` grant with a `personal` ceiling is what lets a bot send a personal part to
  another bot without a card each time; whether messaging needs a ceiling of its own is
  open (spec section 12).
- Revoking a bot revokes its grants in the same transaction (ADR-0138, X5).
