# ADR-0121: Finance reads trusted mail in full and answers card questions from records

Status: Accepted (2026-09-24). The owner decided the rules below in a grill-me interview
on the same day. The work is planned in `docs/architecture/finance-body-read-plan.md`.
Relates to: ADR-0007 (currency from the institution), ADR-0026 (PDF statements from email),
ADR-0031 (institution registry), ADR-0060 (net worth sources), ADR-0071 (semantic email
retrieval, dues from notifications), ADR-0073 (pending actions), ADR-0102 (dues answered
locally), ADR-0110 (tool effects), ADR-0113 (finance forms accounts and due cycles).
Amends ADR-0071 (dues read from the preview only) and ADR-0113 (discovery sees only
registry senders). Supersedes nothing.

## Context

On 2026-09-23 the owner asked the cloud VM "what's my discover credit card bill this
month". The Discover email in the inbox states the statement date, statement balance,
minimum payment and due date in its text. IRIS answered with a
list of eight insurance, bill and dues email subjects. A follow-up to the finance agent
ran six tool calls and then asked the owner for the statement.

What the session log and the VM's databases showed (read-only, 2026-09-23):

- **The dues shortcut is regex-triggered and ignores the institution.**
  `_handle_dues_request_turn` fires on a dues noun plus an owing phrase, before any
  understanding of the question, and returns the dues digest plus a list of email
  subjects. "Discover" was never extracted.
- **The heartbeat never opens an email.** Lane A (ADR-0026) downloads PDF attachments.
  Lane B (ADR-0071 slice 3) extracts one amount and one date from the 200-character Gmail
  preview. Discover sends no PDF, so no statement exists; lane B kept the minimum and
  dropped the balance and the statement date. It also stored a cashback credit as a
  due. Bodies are never stored; only the email agent's `read_email` fetches one, live.
- **The only due was hidden.** The owner marked that due resolved in the Action
  Center on 2026-09-21, so every "pending" view skipped it, and the finance agent had no
  other record of the card.
- **The agent's search was lexical and blind to bodies.** `search_inbox` for "Discover
  Credit Card statement" missed "You have a new statement online". Semantic search
  exists (ADR-0071) but is off on the VM and has no index; it would embed only the subject
  and preview anyway.
- **The trusted registry cannot grow on its own.** Account discovery (ADR-0113) groups
  evidence only from senders already in the registry. The `trust_domain` action is
  offered only from a failed statement, and it types every trusted domain as a utility.

## Decision

1. **A card question is answered with the statement cycle.** For a named account: the
   latest statement's date, balance, minimum and due date; the payments and credits
   posted since; and the remainder of the statement balance. Every figure names the email
   it came from.

2. **The heartbeat reads every email from a trusted institution in full.** "Trusted"
   means an account or biller the owner confirmed. A private-tier model classifies each
   email as statement, payment, credit, due reminder or other; keywords do not decide.
   IRIS stores the extracted facts and the email id, never the body. Registry senders the
   owner has not confirmed keep the preview-only behaviour of ADR-0071.

3. **The sweep learns senders it does not know.** Mail from unknown senders that looks
   financial (a private-tier classification of subject and preview, plus Gmail's own
   category) is grouped by sender domain. The model reads a few bodies per domain to name
   the institution, the account type and the last four digits, and IRIS proposes the
   domain and account together for the owner to confirm or dismiss, capped per sweep.
   Billers are proposed as billers that produce dues, not as accounts. A confirm adds the
   domain to the user registry with the proposed type and backfills that domain's past
   mail; a dismiss is never proposed again.

4. **What a body read produces.** A statement becomes a statement record with source
   `email_body` (a PDF keeps source `pdf`); a posted payment and a credit become
   transactions, and a scheduled payment is a pending transaction until its posted email
   arrives; a reminder with no statement stays a derived due. **Every amount
   and date the model returns must appear literally in the email text**, or the record is
   dropped and an Action Center item says the email could not be read. One exception
   (owner, 2026-09-24): many statement emails never state the statement date (Chase gives
   only the due date, Robinhood only "September 2026"), so a statement date the text does
   not state is left blank rather than fatal, and the statement is ordered by the day it
   was emailed. A month the email names is kept as the statement's month, never turned
   into cycle dates. A body-read
   statement from a confirmed account counts toward net worth as a liability labelled
   "from email". A newer statement for the account replaces the older as current.
   Resolving a due hides it from pending views only; it never hides the statement or its
   payments from a question that names the account.

5. **Questions are routed by meaning; the regex is only a fallback.** The semantic router
   decides the intent, and an entity resolver matches the question against confirmed
   accounts and the registry's names and aliases (data, not code). A named account is
   answered by one deterministic `account_statement` view. When no record exists, the
   same tool searches the institution's mail semantically (the index is turned on and
   built), reads the best matches in full, runs the decision 4 extractor, answers, writes
   the record, and proposes trust when the sender is not trusted. The finance agent gets
   this tool instead of chaining keyword searches. The dues regex applies only when the
   router is unavailable. "What do I owe" with no account named is a digest of the
   records, not a list of email subjects.

## Alternatives considered

- **Give the finance agent `read_email` and keep everything else.** Rejected: it leaves
  every answer to a model loop over keyword search, which is how turn 4 failed, and
  nothing is ever recorded for the next question.
- **Read only the bodies of emails that look like statements.** Rejected: the payment and
  credit emails are needed for the remaining balance, and "looks like" is the keyword
  gate that failed.
- **Read every registry sender's mail, confirmed or not.** Rejected: it reads mail about
  accounts the owner may not hold; confirmation is the trust boundary.
- **Keep model-read figures out of net worth.** Rejected by the owner in favour of the
  literal-match check, which makes an invented figure impossible to store.

## Consequences

- Positive: body-only statements such as Discover's finally become
  records; card questions are answered from data, cite their emails and cost no model
  call once recorded; the registry grows from the owner's own mail.
- Negative: the sweep now calls the private-tier model on financial-looking mail and on
  every trusted email, and fetches full bodies from Gmail. The per-sweep cap and the
  per-domain body limit bound it.
- Net worth changes the day a body-read statement lands; it is labelled so the owner can
  see why.
- Reversible: the lanes and the router-first path are flag-gated; turning them off
  returns to ADR-0071 behaviour. Records written with source `email_body` can be removed
  by source.
