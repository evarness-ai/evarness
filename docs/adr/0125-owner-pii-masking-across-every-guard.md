# ADR-0125: Owner-PII masking across every guard

Status: Accepted (owner, 2026-09-29). PRs 1-4 of 5 built 2026-09-30.
Design: `docs/architecture/owner-pii-masking.md`.
Relates to: plugin-capabilities §4 (capability redaction), ADR-0092 (portfolio name leak),
ADR-0102 (dues web-search leak), issue 0022 (URL-shaped tokens are not secrets),
release-1 decision R14 (no personal content to a cloud tier).
Numbering: 0123 is the Apple Foundation Models ADR; 0124 is left for the
models-by-capability draft, which needs renumbering.

## Context

The harness masks one kind of owner identity: "secret-shaped" literals (14+ characters
with a letter and a digit) taken from the identity documents (SOUL/USER/AGENTS). The
egress guard (`plugins/network_egress.py`), the response check (`plugins/response_safety.py`)
and capability masking (`plugins/capability_redaction.py`) all use that rule, but the first
two extracted it separately, with different exclusions and two caches.

The owner's name, email addresses, phone numbers and postal address are masked nowhere.
`research/privacy.py` and `display_mask.py` have extractors of their own. Step 4 of the
capabilities rollout publishes `mail.read`, which hands mail content to other plugins
with no model and no answer check in between.

## Decision

Owner PII is protected the same way by every guard, from one source.

1. **One module.** `kernel/governance/owner_identity.py` is the one extractor. It is pure,
   imports only the standard library, and tags each literal with its kind.
   `identity_redaction.owner_identity()` is the one cached corpus. Named sources plug into
   that seam, and `config/governance/identity.yaml` holds the kind × guard action table.
2. **Sources.**
   - An owner-edited `identity:` block in the USER.md frontmatter: names, emails, phones,
     addresses, handles and `never_match`.
   - Memris statements whose subject is the owner (`owner_class: Person`, the `$owner`
     binding) and that are confirmed (amendment 5). Which ontology attributes
     count, and as which kind, is IRIS config (`identity.yaml` `ontology_kinds:`), never a
     key in `config/memory/ontology.yaml`. See amendment 1.
   - Account addresses that plugins register through
     `api.register_owner_identity_source` (types in `sdk.identity`).
   - Free text as a fallback, for emails and phones only. No NER.
   - The postal address comes only from the YAML.
   - Handles (`identity: handles`, the `github` and `linkedin` attributes) are their own
     `handle` kind and behave like `name`. See amendment 3.
3. **Capabilities.** Consumers get stable, kind-tagged pseudonyms (`[owner:email#1]`).
   - The manifest grants unmasking per kind: the planner gets name and address, finance
     gets nothing.
   - Secrets are never unmasked.
   - Masking marks the result personal.
4. **Per-guard rules.**
   - A first name alone never denies or halts; it is masked only in web-search arguments.
   - Email, phone and address become placeholders in tier-3 prompts, while names pass.
   - PRE_RESPONSE gains an `audience`. Answers to the owner never halt on owner PII.
   - `github_*` and `mcp_*` get a per-destination allowlist; until then they only log.
   - The display mask and research share the one extractor.
   - The cross-process corpus is at most 5 s stale.
5. **Rollout.** Shadow first, behind `IRIS_GOVERNANCE_OWNER_PII=off|shadow|enforce`, and
   enforced one kind at a time. Enforcing `egress: local-only` is a separate issue.
6. **Sequencing.** PRs 1-3 and capability masking in enforce mode land before step 4
   publishes `mail.read`. `credentials.accounts` carries no content, so it may proceed in
   parallel.

## Amendments (owner, 2026-09-30)

These came out of checking the design against the memris ontology.

1. **No `pii:` key in the ontology.** The memris spec refuses unknown keys
   (`src/memris/ontology/spec.py`, `extra="forbid"`), and memris knows no IRIS vocabulary
   (ADR-0115 decision 2, the `memris-stands-alone` contract). The attribute-to-kind
   mapping lives in `config/governance/identity.yaml` as `ontology_kinds:`. memris and
   `ontology.yaml` are untouched.
2. **Owner subject only.** `name` has domain `Entity`, so contacts and organisations have
   names too. Only statements whose subject is the owner entity count, so a contact's name
   never becomes owner PII.
3. **A `handle` kind.** It covers the `identity:` block's handles and the `github` and
   `linkedin` attributes. It passes in answers to the owner and in tier-3 prompts, is
   masked in web-search arguments, and reaches a capability as a pseudonym.
4. **Deferred.** Birthday and family members' names (`spouse`, `has_child`,
   `family_member`) are out of scope for release 1, recorded as a follow-up. The guard-table
   cells this ADR does not decide stay "proposed" and are confirmed before PR 3.
5. **Sources and invalidation (owner, 2026-09-30, with PR 2).**
   - The memris source is the owner's **confirmed** facts only, not "confidence ≥ 0.8"
     (decision 2 is amended). The store once held `name=ollama` at 1.0, mined from content
     the owner was discussing; confidence is not the owner's word.
   - Invalidation is write-hooks plus fingerprints. In-process writers invalidate the
     corpus at once: the fact store's writes that can change the confirmed set (`put`,
     `resolve`, `set_confirmed`, `forget`, `replace`; not `propose`, whose statements are
     unconfirmed) and the USER.md writers. Each source has a cheap
     fingerprint (file stats; SQLite's `data_version` for `memory.db`), checked at most
     every 5 s, and only a source whose fingerprint moved is read again.
   - Plugin sources are caller-bound: `api.register_owner_identity_source` registers
     `plugin:<name>`, and the manifest's `identity: provides` lists its kinds. A kind it
     returns undeclared is dropped and charged to the plugin; registering with nothing
     declared is refused. `secret` is never providable.
   - PR 2 changes no guard. Guards act on `secret` and `link`, so no new source may
     supply either. `blog`/`website` map to `link` in `identity.yaml` from PR 3, when
     egress's `link` handling can roll out shadow first. `never_match` spares `secret` and
     `link`, the kinds guards act on today, so no owner entry can take either out of a
     guard. PR 2 is fully behaviour-neutral at the guards.
6. **The guard table and PR 3 (owner, 2026-09-30).**
   - Every cell of the build plan's table is decided (no more "proposed"): egress denies
     email, phone and address and logs name and handle; answers to the owner pass every
     PII kind; answers another audience reads mask them; web-search arguments deny email,
     phone and address and mask name and handle. `config/governance/identity.yaml`
     `guards:` holds it, typed per guard and validated: every kind has a row, `secret` is
     masked in capability results and never unmasked, only `secret` may halt an answer to
     the owner, and the `first_name_alone` row (a one-word name literal) never denies or
     halts. `github_*`/`mcp_*` are config (`log_only_destinations`): a deny of email,
     phone or address there is only logged; it can never relax `secret` or `link`.
   - **The link mapping changes egress now, not in shadow.** `blog: link` and
     `website: link` are in `ontology_kinds`, so the owner's confirmed blog and website
     join what egress already refuses. This supersedes amendment 5's "from PR 3, when
     egress's `link` handling can roll out shadow first": the owner took it as PR 3's one
     behaviour change. `link` is declarable by the composition root's sources only; a
     plugin still provides PII kinds only (`OWNER_PII_KINDS`).
   - **Capability pseudonyms are live.** A consumer receives `[owner:<kind>#<n>]` for
     names, emails, phones, addresses and handles (`n` stable per canonical literal for
     the process), unless its manifest grants the kind for that capability:
     `capabilities: uses: [{mail.read: {unmask: [name, address]}}]` (also on `requires`
     entries; read into `capabilities.unmask`). The grant is read for the caller the
     harness stamps, so a plugin cannot use another's. A first name alone reaches the
     consumer unchanged. A result with owner identity is marked `personal`. With no
     readable table, every owner literal is masked and nothing is granted.
   - **Guards stay neutral otherwise.** `owner_pii.decide` (text, guard, audience ->
     span, kind, action) is built and tested; the egress guard, the response check and
     the tier-3 path do not read it until PR 4.
   - **`audience` on PRE_RESPONSE.** `hooks/response_payload.py` builds the payload with
     `audience: owner | other`; every answer is `owner` today. A Telegram group chat and
     text composed for a third party must set `other` once they reach PRE_RESPONSE.

7. **Shadow mode (PR 4, 2026-09-30).**
   - **The flag.** `IRIS_GOVERNANCE_OWNER_PII` is `off` (default; also unset, `0`,
     `false`, `no`) or `shadow`. Any other value, `enforce` included, runs `shadow` with a
     startup warning that names the accepted values and says nothing is masked or denied:
     the operator asked for more than off, shadow is the most this build has, and shadow
     changes no decision, so a mistyped value cannot break a call. Never silently `off`
     (less than was asked) and never an error that stops the kernel. PR 5 adds `enforce`.
     `/governance/state` lists it with its `value`; the settings catalog has it.
   - **One hook, first at three points.** `OwnerPiiShadowHook`
     (`kernel/governance/plugins/owner_pii_shadow.py`) is registered at `PRE_TOOL_USE`,
     `PRE_LLM_CALL` and `PRE_RESPONSE`, priority 1, and returns a bare `allow`: no
     transform, label or tier. It runs first because callers read the final decision
     (the curator reads `dump_flag`, the runner the rewritten `args`), and a hook that ran
     last would replace it. The guards are untouched, so each stays byte-identical; a
     test fires a matrix of calls through kernels with and without the hook and compares
     every decision, final context and real audit row.
   - **What each column reads.** `egress`: a network tool as `network_egress` defines
     one (the same patterns; nothing when that guard is off), over the string leaves of
     the arguments, with `log_only_destinations` marked. `web_search`: a tool declaring
     `sends_to: search_engine` in its manifest, stamped on `PRE_TOOL_USE` by the runner
     (`tool_payload.TOOL_SENDS_TO`); research declares it. By declaration, not by name,
     and in the kernel, because the research plugin may import only the SDK and cannot
     write a kernel row. `tier3`: the prompt when the tier the kernel was given is
     `tier_3`; a local tier is never observed, and how the tier is derived is the
     caller's. `answer_owner` / `answer_other`: the answer, by `audience`.
   - **The row.** The hook's own audit row carries `owner_pii_shadow`: the columns
     checked and, per guard x kind x action (with `first_name_alone` and
     `log_only_destination` flags), the count and up to 20 `[leaf, start, end]` spans.
     Never a literal or the text around it. When a governed call has already resolved
     the audit key, it adds keyed digests (#741) of the distinct literals; it never
     resolves the key itself, and without one it records kinds, counts and spans. An
     observation that fails records the exception's class and still allows. Governed tool
     calls with no key are refused before `PRE_TOOL_USE` (#741), so the tool columns see
     nothing then; `tier3` and the answer columns still observe. `audience` joins the
     kernel's audited payload keys.
   - **Audience.** The turn carries it (`TurnRequest.audience`, published by the
     pipeline with `audience_scope`; the curator reads `current_audience()`). The
     Telegram poller reads `chat.type`: `private` is the owner, anything else
     (`group`, `supergroup`, missing) is `other`; the gateway sends it on `/chat/stream`
     and both chat routes accept it (`other` can only make the check stricter). Text
     composed for a third party is still `owner`: no surface builds one as an answer yet.
     A resumed halted run is `owner` (the approval row does not carry the audience).
   - **Read-back.** `iris governance pii-shadow [--days N] [--json]` and
     `GET /governance/pii-shadow?days=N` render `owner_pii_shadow_summary`: occurrences,
     calls and distinct literals (by digest, when every row had one) per hook point x
     guard x kind x action, the calls each guard read, and the calls it could not observe
     (no corpus, no table, an error). It is what the owner reads to choose PR 5's order.
   - **Open points to revisit (before or in PR 5).**
     - Every shadow firing writes a row, including calls where nothing was checked or
       found: those rows are the per-guard denominator. The cost is one row per tool
       call, model call and answer while shadow is on.
     - At `PRE_LLM_CALL` the hook runs before the redaction filter (priority 20), so the
       tier-3 `secret` count may be slightly high (secrets redaction would scrub anyway).
     - Only argument values are scanned, not argument names; `network_egress` scans
       `str(args)`, names included. Integers are scanned, booleans are not.
     - `research` is both a network tool and a search-engine tool, so its calls get an
       `egress` and a `web_search` entry: both guards will act on it in PR 5.
     - The tier-3 column trusts `ctx.tier == "tier_3"` as given. If locality ever moves
       to a payload key instead of the tier (see `fix/govern-tiers-by-locality`), this
       check must read that key.
     - Still `owner` by default: text composed for a third party, and a resumed halted
       run (the approval row does not store the audience).

## PRs

1. A refactor with no behaviour change, onto the shared module. **Built:** two kinds,
   `secret` and `link`. The egress guard refuses both, as before. The response check and
   capability masking act on `secret` only, as before (issue 0022).
2. Sources and invalidation. **Built:** the new kinds, the three sources, plugin sources,
   `identity.yaml` `ontology_kinds:`, invalidation (amendment 5). No guard asks for a new
   kind; a test pins every guard's literal set to the documents-only corpus.
3. Per-kind matchers. **Built:** the matchers, the table, the decision function,
   capability pseudonyms and grants, the link mapping, `audience` (amendment 6).
4. Shadow mode. **Built:** the flag, the shadow hook, the row, audience from the turn and
   the Telegram chat type, the read-back (amendment 7).
5. Enforce, one kind at a time.

## Consequences

- A new kind reaches every guard from one place, and each guard asks for the kinds it acts
  on. Adding a kind changes no guard that does not ask for it.
- The guards can no longer disagree about what the owner's identity is. Before PR 1 they
  extracted it twice, from the same text, with different rules.
- Registering a provider now clears the cached corpus, so a late registration takes
  effect. Before, both guards kept whatever they had cached first.
