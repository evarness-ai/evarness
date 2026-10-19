# ADR-0127: A turn the system opens, and the first-chat welcome

Status: Accepted (owner, 2026-10-01). Built on `feat/first-chat-welcome`, stacked on
`fix/trace-handler-turns`.
Relates to: OSS plan decision 9 (one turn pipeline), deterministic-path parity
(`docs/architecture/deterministic-path-parity.md`), ADR-0077 (deterministic intercepts),
`docs/concepts/deterministic-handlers.md`.

## Context

On a fresh install the console's Sessions, Call trace and Governance screens are empty
until someone chats, and with no model pulled nobody can. The owner decided that the
first time a chat opens, IRIS runs a welcome turn by itself: a deterministic handler,
instant and model-free, that goes through the real governed pipeline so the three screens
hold real data at once. It fires once per `IRIS_HOME`, the harness decides when, and every
chat surface reaches it the same way.

Two things in the code stood in the way:

1. **The pipeline had no turn without a user message.** `screen` logs the user message
   and screens it; `intercept` hands the message to handlers that read it. The resume
   path (`RESUME_STAGES`) ran without one, but nothing could answer there.
2. **Sessions and Call trace start a turn at `user_message`.** A turn without one is not a
   turn to them. (They also hid every handler-answered turn; that was fixed first, on its
   own branch: `handler.end` / `guard.end`, see `fix/trace-handler-turns`.)

## Decision

1. **A system-opened turn.** `TurnRequest.opener` names a deterministic handler declared
   under `openers:` in `config/intercepts.yaml`; `IrisRuntime.open_turn(opener, ...)` runs
   it through `OPENER_STAGES`:
   - `open` logs the turn's start as **`turn_open`** (opener name and a label, never a
     `user_message`: no words are put in the owner's mouth), then `turn.start`, runs the
     opener and logs `handler.end`.
   - `guard` gives the answer the same model-free response check (`PRE_RESPONSE`) a
     deterministic handler's answer gets, audited with `deterministic: true` and the
     handler's name, with the output guard too when the row says `guard_output`.
   - `record` writes `agent_response` to the session log.
2. **What is not covered, and why.** There is no input, so there is no input screen
   (`PRE_TURN`) and no classification: nothing arrived to screen. The opener's text is the
   harness's own words, which is what the response check exists for. The model path is
   absent: an opener answers, or the turn fails. Session memory gets nothing (it stores
   user/assistant pairs, and there was no user), and no learning signal is recorded.
3. **Readers split turns on `TURN_OPENING_KINDS`** (`user_message`, `turn_open`):
   Sessions, Call trace, chat replay and `/replay`. A system-opened turn's request is its
   label ("First-chat welcome"); chat replay shows only the assistant message; Call trace
   draws `IrisRuntime.open_turn` → handler → response check and marks the trace
   `opened_by_system`.
4. **The welcome** (`runtime/welcome.py`, `runtime.welcome`):
   - **Due once per `IRIS_HOME`**, recorded in `$IRIS_HOME/welcome.json` (session id,
     time, channel, text) only after the turn completed, so a failed turn is offered
     again. An unreadable record still counts as done.
   - **Fresh homes only** (owner, 2026-10-01). A home that already has a conversation,
     as the Sessions list counts them (session logs under `IRIS_HOME`, playground / eval
     / test runs left out), gets no welcome: `welcome.json` records it as `skipped` with a
     reason, no turn runs, and the endpoint answers `created: false, skipped: true`.
   - **One endpoint**, `POST /chat/welcome` (authenticated like `/chat`, in
     `_UNGATED_WRITE_PATHS` like `/chat`): runs it if due, else returns the one that ran,
     with `created` saying which. Idempotent, so every surface calls it on open.
   - **Its own session** (12 hex, the shape of an API-started chat), turn 0, so its trace
     id is `<session>~0`.
   - **Text from the registry.** Each mounted plugin's manifest `summary` (a new optional
     field: one owner-facing line; `description` stays for developers) becomes a line of
     "what I can do here". The rest of the wording is `config/welcome.yaml`.
5. **Surfaces.** The web console's Chat calls the endpoint when it opens and, when the
   welcome is new, switches to its session so the owner's first question continues it.
   The CLI REPL and `iris -p` call it at start and print it when new. The Telegram
   gateway calls it on `/start` or the first message and sends it first when new.

## Alternatives considered

- **A trigger message** ("(opened chat)") answered by an ordinary intercept. Smallest
  change, but it logs words nobody typed as the owner's, they replay as a user bubble, and
  any typed message equal to the trigger would fire it.
- **Writing the welcome into the stores directly.** Rejected by the owner: the point is
  real data, produced by the pipeline, that the trace and the ledger can vouch for.
- **The session log as the "once" record.** Logs are archived after 30 days and a session
  can be removed (ADR-0119); either would bring the welcome back.

## Consequences

- Every surface shows the welcome once per home, from one harness decision.
- An install that already has conversations never gets the welcome: the first ask after
  the upgrade records it as skipped.
- `turn_open` is a new session-log event kind; a reader that splits turns must use
  `TURN_OPENING_KINDS`.
- Plugins may declare `summary`; one without it is not listed.
- Reversible: delete `openers:` and the endpoint; the readers keep working on logs that
  hold `turn_open` events (they become turns without a user message).
