# ADR-0113: The finance agent forms the user's accounts and card due cycles from the evidence

Status: Accepted (2026-09-15)
Relates to: ADR-0007 (currency from the institution), ADR-0031 (institution registry), ADR-0060 (net worth sources), ADR-0071 (dues from notifications), ADR-0073 (pending actions), ADR-0110 (tool effects), ADR-0112 (inbox analysis; implausible dates).
Supersedes nothing.

## Context

"Can you give me the list credit card accounts that I have so far?" had no answer in
finance. The owner's framing: email is the source; finance should hold bank accounts, net
worth, the credit cards and their due dates, and those patterns must be formed by the
finance agent.

Verified against the code and the real record:

- **Accounts were only ever registered by hand** (`iris finance account add`). The record
  held five: one bank account and four investment accounts, no cards.
- **The evidence that accounts exist arrived anyway and stayed unlinked.**
  `ingest._resolve_account_id` links a statement only to an account that is already
  registered. So two card statements, and statements from three institutions, sat with
  `account_id NULL`, feeding neither cash nor liabilities. Eight due notices extracted
  from email name issuers but no account. The mailbox holds statement mail from about 20
  registered issuers.
- **Account type came only from the institution.** `chase_us` is a bank in the registry,
  yet "Your Prime Visa payment is due" is a card.
- **Nothing read a card's due dates as a cycle.** `recurring.py` does cadence detection,
  but only over bank debits.
- **No loop tool listed accounts.** `build_accounts_digest` served only a keyword
  intercept, whose pattern did not know cards.
- **Evidence was being lost to bad reads.** The dues extractor accepted "2023-07-14" from
  a 2026 notice.

## Decision

1. **Discovery proposes; the user decides.**
   - `account_discovery.discover_accounts` groups three kinds of evidence by
     `(institution, account type)`:
     - unlinked statements;
     - derived due notices;
     - statement or due mail from registry issuers. A message counts if it passes the
       dues phrase gate or the institution's own statement subject pattern.
   - A notifier's `notices_for` credits the account it bills for. Product words in a
     subject or issuer beat the registry type (vocabulary in `account_discovery.yaml`).
   - Billers (utility, telecom, insurance) are dues, not accounts.
   - Candidates are not keyed by inbox: one issuer mailing two inboxes is one proposal.
     Accounts already in the record are never proposed.
   - Results persist to `finance_account_candidates` as `proposed`. Re-discovery refreshes
     the evidence and never overwrites a `confirmed` or `dismissed` decision.
2. **A confirm is the only write of an account, and it completes the record.**
   - `confirm_candidate` creates the `finance_accounts` row and links the unlinked
     statements of that institution and document type. From then on the statements count
     in net worth: card amounts due as liabilities, bank balances as cash.
   - `dismiss_candidate` stops the proposal for good.
   - Decisions reach the user on every surface:
     - **Action Center:** a pending-action pair ("Yes, it's mine" / "Not mine", generic
       `execute` kind). The ingest heartbeat's `sync_finance_pending_actions` runs
       discovery first.
     - **Chat:** the loop tool `confirm_finance_account` (effect `write`, `confirm: once`).
     - **CLI:** `iris finance account discover | confirm | dismiss | overview`.
3. **Card due cycles are read from stated due dates and predicted only when regular.**
   - `due_cycle` takes the latest stated due per month. An upcoming stated date is the
     actual next due.
   - Otherwise, with at least `min_cycles` months whose day-of-month spread is at most
     `max_day_spread`, it predicts the next occurrence of the median day, clamped to month
     end. The prediction is rendered as "next due ~DATE (predicted from N cycles; usually
     the Dth)".
   - An irregular or single-cycle history predicts nothing.
   - Due dates more than 180 days from the notice's own date are ignored here and dropped
     at extraction (`PLAUSIBLE_DUE_WINDOW_DAYS`).
4. **One overview, on every surface.**
   - `build_account_overview` / `render_account_overview` list accounts by type (bank,
     card, loan, investment, wallet). Registered accounts show their latest balance or
     amount due and cycle; unconfirmed discoveries are marked with their evidence.
     Registered-account net worth closes the list.
   - The same text comes from the new read tool `finance_accounts`, the `accounts_request`
     intercept (unless the user named an institution, which keeps the existing digest),
     and `iris finance account overview`.
   - The accounts pattern now covers cards and loans ("which credit cards do I have",
     "list credit card accounts").
   - Manifest guidance sends account questions to `finance_accounts` first, with
     `analyze_inbox` only when finance has nothing. `analyze_inbox`'s examples no longer
     name accounts.

## Consequences

- On the real record (read from a copy): 5 registered accounts, and 16 found awaiting
  confirmation. Of those, 8 are credit cards (Chase, Citi, Capital One, Discover, ICICI,
  Barclays, U.S. Bank, Robinhood), the rest bank, loan and investment accounts. Each shows
  its evidence; ICICI shows its stated due date.
- Due-date history is thin today: the notice extractor reads only the newest 200 messages
  per run. Predictions appear as notices accumulate month over month; a backfill over
  older mail would speed that up at the cost of model calls.
- Without a last-4, two cards from one issuer are one account. Confirming links every
  unlinked statement of that institution and type to it.
- Confirming changes net worth, because linked statements begin to count. This is the
  intended correction, stated in the confirm prompt.
- Issuer promotional mail that matches a registry subject pattern can raise an evidence
  count, but it never creates an account on its own, and the user confirms.
- Reversible: discovery is read-only until a decision. A confirmed account is an ordinary
  account (`deactivate_account`). Dropping `finance_account_candidates` loses only
  proposals and dismissals.

## Follow-up (same day): deciding an account is an intercept, not a tool call

The first live test of the confirm flow failed, and failed dishonestly.

Every decision turn — "the Discover card is mine, confirm it", the comma-separated list
of seven cards, "Barclays US Cards - confirm" — classified as `communication`
(confidence 0.9) and reached the **email agent**, which has no confirm tool. It searched
the mailbox instead, and on the first turn replied *"The Discover Card has been confirmed
as one of your accounts"*. Nothing had been written: the record still held five accounts
and all 17 candidates were still `proposed`.

The decision above ("Chat: the loop tool `confirm_finance_account`") assumed the turn
would reach the finance loop. It does not, and the house rule already says why: a
grounded action must not depend on a model choosing a tool (ADR-0097, ADR-0102, and the
measured `configure_brief` result).

So deciding a discovered account is now the `account_confirmation` **intercept**, ahead
of routing, registered in the chain between `statement_email_details` and
`accounts_request`:

- Dismissal is read before confirmation, so "isn't mine" is never "mine".
- One account, a comma-separated list, or "all" / "all cards" (scoped by the type named).
  **Names win over "all"**: a turn that lists three cards and says "confirm all" decides
  those three, never an account the user did not name.
- An account is a target when the turn carries one of its identifying words — its label
  minus the words every label shares — which is what makes a list work with no splitting.
- A turn naming an account that is already confirmed or dismissed is answered from the
  record ("Already in your accounts"), never left to routing.
- It fires only when proposals exist, so an ordinary turn costs nothing, and every
  phrase comes from `account_discovery.yaml`.

The `confirm_finance_account` tool stays for the loop; the intercept is what the user
actually reaches. Verified by replaying the failed session's exact turns against a copy
of the real record: 1 confirmed, then 7 confirmed with 2 statements linked, then the
repeat answered from the record, with inbox questions still routing normally.

## Related

ADR-0007, ADR-0031, ADR-0060, ADR-0071, ADR-0073, ADR-0097, ADR-0102, ADR-0110, ADR-0112.
