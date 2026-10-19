# ADR-0112: Reminder text is summarised, dates are resolved on the user's clock, a clarifying question is not consent, and inbox analysis reads every match

Status: Accepted (2026-09-15)
Relates to: ADR-0075 (calendar write authority), ADR-0102 / ADR-0104 (dues intercept), ADR-0106 (continuations), ADR-0110 (tool effects; confirm once), the multi-step loop plan (decisions 3 and 8).
Narrows ADR-0110's confirm-once evidence. Supersedes nothing.

## Context

One live session (2026-09-15) showed three failures:

1. **"can you remind me tomorrow at 10 am for a AI training ?"** was saved as
   *"Can you for a AI training ?"*. The `reminder_creation` intercept built the subject by
   removing the trigger, date and time from the sentence with regex. The meeting title
   already had a model-first extractor; reminders never got one. Nothing asked the user when
   no subject was left.
2. **Two reminders were set, but "what's my schedule tomorrow?" listed one.** Both writes
   went through the loop's `create_reminder` tool, and both had the wrong date:
   - "tomorrow at 5 pm", asked at 23:53 on 09-14, was saved on **09-16**.
   - A follow-up was saved on **09-11**, already in the past, and its notification fired
     immediately.

   The ReAct prompt (`_build_react_prompt`) carried no current date, and the tool accepted
   only an ISO date. The model computed "tomorrow" from the last date it had seen in recent
   turns.

   The second write should never have happened. "how about 5 PM?" led the model to ask
   *"What task or event are you checking for 5 PM?"*, and the reply "Python training" went
   straight to a write. The confirm-once rule counted **any** `ask_user` in the run as
   consent.
3. **"can you give me the list credit card accounts that I have so far?"** got a dump of
   dues-related inbox subjects in 250 ms. The `dues_request` intercept fires on a dues noun
   ("credit card") plus any personal pronoun. The semantic router had confidently
   classified the turn as `communication`, but intercepts run before classification.
   Even routed correctly, the loop could not have answered: `search_inbox` returns the top
   15 hits, which suits a lookup, not an analysis.

## Decision

1. **A reminder's text is a model summary of what it is for.**
   - `llm_reminder_task` does this on the `task_planning` tier (the same kill-switch as
     meeting titles, `IRIS_MEETING_TITLE_LLM`). The deterministic extraction is its
     fallback, and now also strips polite wrappers and leading connectors (vocabulary in
     `nlu.yaml`).
   - When the model answers `NONE`, or nothing is left, the intercept opens an owned
     `question` continuation (owner `reminder_creation`, date and time in the payload) and
     asks *"What should I remind you about?"*. The next turn's answer completes the
     reminder. A question or a new reminder request drops it.
   - A to-do with a clock time ("add a to-do to … at 5 pm") is a reminder trigger.
2. **Dates are resolved on the user's clock, never by the model.**
   - The ReAct prompt opens with the local date and time, for the reason the general
     handler pins them there: prompts are trimmed tail-first.
   - `create_reminder` takes the user's own words for the day ("tomorrow", "next friday")
     and resolves them with the shared `dateparse` on the parsers' clock. It also accepts
     spoken times ("5 pm").
   - `CalendarHandlers.create_reminder`, the single write path, refuses a moment that has
     already passed.
3. **Confirm-once evidence is an `ask_user` that comes after an attempt at that same
   tool.** `_run_asked_user(steps, tool_name)` holds only when the run proposed the write,
   was held, and then asked. A clarifying question asked earlier is not consent, and
   consent to one write tool is not consent to another. The core's write-tool guidance in
   the prompt now describes that order.
4. **The router decides the domain for the dues intercept; vocabulary decides the
   sub-question.**
   - A bare dues noun now needs an owing frame (due, owe, pay, premium, bill, balance, …),
     not just a pronoun.
   - After the vocabulary matches, the intercept consults `HarnessServices.classify_intent`
     and declines when the router **confidently** (source `semantic`) places the turn
     outside `finance`. When the router is unsure, off, or failing, the vocabulary alone
     decides, so the intercept never silently disappears.
   - This gate is **not** applied to `accounts_request`. Measured: the router labels
     "list my bank accounts" as `communication`, so a veto there would break it.
5. **`analyze_inbox`, a read tool for which / how-many / list-all questions over email.**
   - It runs the FTS search with no top-N cap (bounded at 1000), OR-broadens on a miss, and
     groups every match by sender: counts, date span, categories, and up to three distinct
     example subjects with account numbers masked.
   - The model is told to name each distinct item once. The grouping is pure
     (`iris_personal/email/analysis.py`), and the tool is on both the loop and the email
     agent through `build_email_tools`.
   - The shared grounding check (`_query_grounded`) now requires both tokens to be at
     least 4 characters for a prefix match. The user's "i" had been prefix-matching every
     invented topic.

## Consequences

- Reminder text reads like a reminder. On the tier-2 model: "Attend AI training", "Read
  about Python training", "Pick up kids from school". "remind me about it tomorrow at 9"
  asks instead of saving "It".
- A reminder with a subject costs one tier-2 call before it is written.
- A model that asks first and then writes will be held once and ask again. A double
  confirmation is the price of never writing on a clarification answer.
- On the real mailbox, "credit card" analysis reads 82 emails from 22 senders; the
  intercept showed 8 subjects.
- Reversible: `IRIS_MEETING_TITLE_LLM=0` restores deterministic subjects, and
  `IRIS_DUES_INTERCEPT=0` removes the intercept. The confirm-once change is one function.

## Related

ADR-0075, ADR-0102, ADR-0104, ADR-0106, ADR-0110, ADR-0111.
