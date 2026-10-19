# Architecture decisions

This is the decision log Evarness grew up with: one record per non-obvious or trade-off-heavy decision, with its context, the choice, the alternatives rejected and the consequences, as they were understood **at the time**.

Read it with three things in mind.

- **A record is history, not a description of the current system.** It is not edited when the code changes. The current system is described in [Architecture](../concepts/architecture.md) and the pages under `docs/architecture/`. When a record and the code disagree, the code is current and the record explains how it got there; a superseding record says so in its status line.
- **They predate the rename.** Until 0.2.0 the project was IRIS, and the records say IRIS, `iris_harness`, `IRIS_*` and `~/.iris`. Read `evarness`, `EVARNESS_*` and `~/.evarness`.
- **Some decisions concern code that is not in this repository.** The finance, calendar, file-manager, planner and coding-agent domains live in a private tree. Their records are kept here because the harness decisions reference them; the *Where* column says `private tree`.

**Status pass, 2026-10-18.** Every record cited by the code whose named files no longer existed, and every Proposed record, was checked against the code. Where something changed, a dated *Status pass* line sits under the record's own status and the *Where* column below says where the code lives now, or that it is superseded, private, implemented or not implemented. A record without that line was not re-checked and reads as written.

Records numbered 0001 to 0134 were written between May and October 2026. Thirteen of them (0078 to 0086, 0092, 0097, 0101 to 0103) were sections of the project decisions log rather than standalone files and were extracted on 2026-10-18; each says so under its title. Two records share the number 0123; both are kept. Numbers with no record were never written.

| # | Title | Status | Where | Cited by code |
|---|---|---|---|---|
| [0001](0001-singular-iris-agent-vs-sub-agents.md) | Singular IRIS agent, skill packs only — no sub-agents | Accepted |  |  |
| [0002](0002-privacy-aware-tier3-sensitivity-per-category.md) | Privacy-aware Tier 3 — local vs cloud resolved by per-category sensitivity | Accepted |  | yes |
| [0003](0003-keychain-credentials-over-vault.md) | OAuth tokens in the macOS Keychain via `keyring`; no custom vault | Accepted | `kernel/governance/vault/credentials.py` | yes |
| [0004](0004-reuse-wiki-for-cross-domain-correlation.md) | Reuse the existing wiki/knowledge subsystem as the cross-domain correlation engi | Superseded |  |  |
| [0005](0005-task-vocabulary-and-soft-migration-of-reminders.md) | Task vocabulary + soft migration of the legacy reminders module | Accepted | `evarness/services/tasks/` | yes |
| [0006](0006-pdf-from-email-finance-ingestion-no-plaid.md) | Finance ingestion via PDF-from-email + manual drop; no Plaid/scraping in core | Accepted |  | yes |
| [0007](0007-multi-currency-no-implicit-fx.md) | Multi-currency model — currency-required, group-by-currency aggregation, no impl | Accepted |  | yes |
| [0008](0008-r2-r3-calendar-write-split.md) | R2/R3 split for calendar writes — private events auto, attendee events prompt | Accepted |  |  |
| [0009](0009-no-sandbox-community-skill-trust-model.md) | No sandboxing for community skills — explicit trust model + governance enforceme | Accepted |  | yes |
| [0010](0010-entity-extractor-tightening-not-rewrite.md) | Entity extractor — tighten regex + hint validation, not rewrite | Accepted | `memory/knowledge/entity_extractor.py` | yes |
| [0011](0011-keychain-credentials-module-shape.md) | Keychain credentials module — implementation shape | Accepted |  |  |
| [0012](0012-manifest-schema-extension-shape.md) | Manifest schema extension — implementation shape | Accepted |  | yes |
| [0013](0013-event-bus-promotion-shape.md) | Event bus promotion — implementation shape | Accepted | `foundation/eventbus/` | yes |
| [0014](0014-tasks-subsystem-implementation-shape.md) | Tasks subsystem — implementation shape | Accepted | `evarness/services/tasks/` | yes |
| [0015](0015-pdf-unlock-helper-shape.md) | PDF unlock helper — implementation shape | Accepted | private tree |  |
| [0016](0016-email-accounts-registry-shape.md) | Email accounts registry — implementation shape and DB location | Accepted |  | yes |
| [0017](0017-email-classification-dynamic-hierarchical-self-learning.md) | Email classification — dynamic, hierarchical, self-learning categories | Accepted |  | yes |
| [0018](0018-email-bootstrap-categories-cli-shape.md) | `iris email bootstrap-categories` CLI — implementation shape | Accepted | `evarness_personal/plugins/email_workflows/cli.py`, `discovery.py` | yes |
| [0019](0019-categories-store-and-accept-cli-shape.md) | `categories` data model + `iris email accept-categories` CLI — implementation sh | Accepted | `evarness_personal/email/category_store.py` | yes |
| [0020](0020-drift-correction-followup.md) | Drift correction — user-driven corrections for systematic LLM naming errors | Accepted |  | yes |
| [0021](0021-email-triage-skill-shape.md) | `email-triage` skill — implementation shape | Superseded by ADR-0022 |  | yes |
| [0022](0022-resource-aware-triage-revision.md) | Resource-aware triage — pure-kNN per email, on-demand LLM batch review | Accepted |  | yes |
| [0023](0023-knn-gate-and-holdout-labeling.md) | kNN-gate measurement + holdout-labeling tools | Accepted |  | yes |
| [0024](0024-self-learning-substrate-scope.md) | Self-learning substrate — Phase 1 scope | Accepted |  | yes |
| [0025](0025-email-wiki-ingestion-shape.md) | email → wiki ingestion shape | Accepted | `email_workflows/wiki_ingestion.py` and `evarness/memory/knowledge/event_subscribers.py` | yes |
| [0026](0026-email-search-fts-shape.md) | email-search FTS shape | Accepted |  | yes |
| [0027](0027-tasks-goals-cli-shape.md) | `iris task` + `iris goal` CLI — implementation shape | Accepted |  |  |
| [0028](0028-email-followup-shape.md) | `email-followup` skill — implementation shape | Accepted |  |  |
| [0029](0029-morning-brief-integration-shape.md) | Morning brief Phase 2 integration — implementation shape | Accepted |  |  |
| [0030](0030-delivery-only-reminder-shape.md) | Delivery-only Reminder primitive + channel routing — implementation shape | Accepted |  |  |
| [0031](0031-finance-data-layer-shape.md) | Finance data layer — schema, models, and hybrid institution registry | Accepted |  |  |
| [0032](0032-pluggable-secret-store.md) | Pluggable secret store — keyring default, encrypted file vault optional | Accepted |  | yes |
| [0033](0033-statement-extraction-shape.md) | Statement extraction — Tier-3 LOCAL into a strict schema, injectable extractor | Accepted |  |  |
| [0034](0034-pdf-extraction-engine.md) | PDF extraction engine — pypdfium2 + pdfplumber, no AGPL; deterministic decoding | Accepted |  |  |
| [0035](0035-deterministic-numbers.md) | Deterministic numbers — don't let the LLM transcribe digits; read cells + reconc | Accepted | private tree |  |
| [0036](0036-per-document-type-extractors.md) | Per-document-type extractors — contract notes → trades, CAS → holdings | Accepted | private tree |  |
| [0037](0037-finance-cli-and-networth.md) | `iris finance` CLI + per-currency net-worth semantics | Accepted | private tree |  |
| [0038](0038-transaction-categorization.md) | Transaction categorization — hybrid rules + embedding kNN + corrections | Accepted |  |  |
| [0039](0039-finance-monitor-and-brief.md) | Finance Monitor heartbeat + morning-brief integration | Accepted | private tree | yes |
| [0040](0040-calendar-provider-abstraction-ics-first.md) | Calendar agent foundation — provider abstraction, ICS first, one ICS implementat | Accepted |  |  |
| [0041](0041-native-google-calendar-provider-sync.md) | Native Google Calendar provider — OAuth, provider mapping, syncToken delta | Accepted | private tree |  |
| [0042](0042-calendar-write-governance-boundary.md) | Calendar write governance as a boundary module (not a governor-policy extension) | Accepted | private tree |  |
| [0043](0043-calendar-routine-mirror-mcp.md) | Calendar C3 — meeting-prep routine, reconcile-based task/bill mirror, stdlib MCP | Accepted | private tree | yes |
| [0044](0044-planner-deterministic-daily-plan.md) | Planner agent foundation — deterministic daily-plan synthesis | Accepted | private tree |  |
| [0045](0045-planner-parallel-execution-agent-type.md) | Planner P1 — parallel wave execution + a registered planner agent type | Accepted | `evarness/agent/agent_executor.py` | yes |
| [0046](0046-planner-llm-narrative-brief-headline.md) | Planner P2 — grounded LLM plan narrative (brief headline dropped) | Accepted |  |  |
| [0047](0047-document-rag-foundation.md) | Document RAG foundation — index the user's docs (connector, not owner), cited re | Accepted |  |  |
| [0048](0048-obsidian-vault-connector.md) | Obsidian vault connector — front-matter, wikilinks, incremental sync | Accepted |  |  |
| [0049](0049-rag-grounded-qa-mcp-exposure.md) | RAG grounded cited Q&A + tag scope + MCP exposure | Accepted |  |  |
| [0050](0050-rag-pdf-image-ocr-ingestion.md) | RAG PDF + image ingestion with opt-in OCR | Accepted |  |  |
| [0051](0051-filemanager-secure-foundation.md) | FileManager secure foundation — deny-by-default confinement, secret-aware reads, | Accepted |  |  |
| [0052](0052-filemanager-governed-mutations.md) | FileManager governed mutations — R2/R3 + soft, reversible ops | Accepted |  |  |
| [0053](0053-filemanager-gdrive-icloud.md) | FileManager cloud — Google Drive (read-only) + iCloud Drive as a local root | Accepted |  |  |
| [0054](0054-filemanager-redaction-tiered-sharing.md) | FileManager advanced security — redaction-on-share + sensitivity-tiered reads +  | Accepted |  |  |
| [0055](0055-system-agent-status-introspection.md) | System agent — user-facing status + IRIS self-introspection | Accepted |  |  |
| [0056](0056-filemanager-interactive-approval.md) | FileManager interactive approval — agent requests, human approves destructive op | Accepted |  |  |
| [0057](0057-rag-docx-ingestion.md) | RAG Microsoft Word (.docx) ingestion | Accepted |  |  |
| [0058](0058-filemanager-pending-ttl.md) | FileManager pending-approval TTL — stale destructive requests expire | Accepted |  |  |
| [0059](0059-planner-brief-synthesis-slot.md) | Planner brief "today's plan" headline via a synthesis slot | Accepted |  | yes |
| [0060](0060-finance-source-of-truth.md) | Finance source-of-truth — authority, identity-keyed dedup, bounded agent reasoni | Accepted |  |  |
| [0061](0061-finance-document-storage-policy.md) | Finance document storage & retention policy | Accepted | private tree | yes |
| [0062](0062-filemanager-custodian-and-catalog.md) | — FileManager as custodian: the File Catalog + custody API (FMX0) | Accepted |  | yes |
| [0063](0063-finance-custody-client.md) | — Finance as the first FileManager custody client (FMX1) | Accepted |  | yes |
| [0064](0064-filemanager-media-indexer.md) | — FileManager media + iCloud Drive indexer (FMX2) | Accepted |  | yes |
| [0065](0065-filemanager-apple-photos.md) | — FileManager Apple Photos library indexer (FMX3) | Accepted |  | yes |
| [0066](0066-filemanager-rag-catalog-and-writer-scope.md) | — RAG documents in the catalog; custody scope for other writers (FMX4) | Accepted |  | yes |
| [0067](0067-custodian-purge-tier-guard.md) | — FileManager never deletes files it doesn't own (purge tier guard) | Accepted |  | yes |
| [0068](0068-curator-escalation-judge.md) | — Curator-driven tier-escalation judge (model cascade) | Accepted |  | yes |
| [0069](0069-system-health-harness-capability.md) | System Health — a harness capability with local-on / network-opt-in probing | Accepted | `evarness/services/health/`, `sdk/health.py`, `harness_api/health_routes.py` | yes |
| [0070](0070-sandbox-replay-eval-for-experiments.md) | Sandbox replay-eval harness for learning experiments | Accepted | `runtime/self_learning_loop.py`, `tools/sandbox/` | yes |
| [0071](0071-semantic-email-retrieval-and-dues-from-notifications.md) | Semantic email retrieval + dues-from-notification extraction | Accepted | `evarness_personal/email/semantic_index.py`, `agent_tools.py` | yes |
| [0072](0072-judge-gated-feedback-and-clarify-loop.md) | Judge-gated, optional feedback & clarify loop | Accepted |  | yes |
| [0073](0073-pending-actions-and-agent-console.md) | Pending actions — background jobs surface actionable blockers as tasks, with an  | Accepted | `services/tasks/pending_actions.py`, `runtime/action_center.py`, `harness_api/action_center_routes.py` | yes |
| [0074](0074-per-agent-console.md) | Per-agent console — an ops dashboard per registered agent | Accepted | `agent/agent_metadata.py`, `runtime/agent_console.py`, `harness_api/agent_routes.py` | yes |
| [0075](0075-calendar-single-write-authority.md) | Calendar as the single write authority (events, invites, appointments, reminders | Accepted | private tree | yes |
| [0076](0076-in-chat-approval-for-consequential-actions.md) | In-chat approval for consequential actions | Accepted |  | yes |
| [0077](0077-converge-on-one-governed-agentic-loop.md) | Converge on one governed agentic loop — tools as universal actionable items | Accepted |  | yes |
| [0078](0078-generic-surface-feedback-suppression-spine.md) | Generic surface-feedback + suppression spine | Accepted (PRD log) | `services/learning/suppression.py`, `harness_api/session_routes.py`, `runtime/react_tools.py` | yes |
| [0079](0079-window-aware-conversation-auto-compaction.md) | Window-aware conversation auto-compaction | Accepted (PRD log) |  | yes |
| [0081](0081-context-health-surface-one-view-of-context-management.md) | Context-health surface (one view of context management) | Accepted (PRD log) | `agent/context_health.py`, `runtime/session_memory.py`, `cli/context_health.py` | yes |
| [0082](0082-learn-before-you-forget-mine-the-compaction-archived-span.md) | Learn before you forget (mine the compaction-archived span) | Accepted (PRD log) |  | yes |
| [0083](0083-learning-experiment-console-hot-toggle-no-restart.md) | Learning experiment console (hot-toggle, no restart) | Accepted (PRD log) |  | yes |
| [0084](0084-act-on-context-health-on-demand-compaction.md) | Act on context-health (on-demand compaction) | Accepted (PRD log) |  | yes |
| [0085](0085-measure-the-memory-garbage-in-garbage-out-guarantees.md) | Measure the memory garbage-in / garbage-out guarantees | Accepted (PRD log) |  | yes |
| [0086](0086-agent-self-management-tools-act-on-your-own-brain.md) | Agent self-management tools (act on your own brain) | Accepted (PRD log) |  | yes |
| [0092](0092-portfolio-answered-locally-kill-the-web-search-name-leak.md) | Portfolio answered locally — kill the web-search name leak | Accepted (PRD log) |  |  |
| [0097](0097-portfolio-vs-market-live-index-context-not-a-fake-benchmark.md) | Portfolio vs market — live index context, not a fake benchmark | Accepted (PRD log) |  | yes |
| [0101](0101-apple-calendar-read-back-iris-can-finally-see-your-schedule.md) | Apple Calendar read-back — IRIS can finally see your schedule | Accepted (PRD log) | private tree | yes |
| [0102](0102-personal-finance-questions-never-touch-the-web-dues-intercep.md) | Personal-finance questions never touch the web — dues intercept + research guard | Accepted (PRD log) |  | yes |
| [0103](0103-action-requests-escalate-to-a-capable-model-generic-not-fina.md) | Action requests escalate to a capable model (generic, not finance-specific) | Accepted (PRD log) |  | yes |
| [0105](0105-user-fact-store-coherence.md) | User-fact store coherence — single write-seam + reconcile doctor, no DB merge | Accepted |  | yes |
| [0106](0106-session-owned-continuations.md) | Session-owned continuations — every pending question has an owner | Accepted |  | yes |
| [0107](0107-poststep-on-the-streaming-loop.md) | The PostStep evaluator does not run on the streaming loop | Accepted |  | yes |
| [0108](0108-approval-queue-wiring-and-the-web-channel.md) | The approval queue, connected — and a channel for the web | Accepted |  | yes |
| [0109](0109-the-layered-tree.md) | The layered tree — `src/iris_harness` as the harness's building blocks | Accepted |  |  |
| [0110](0110-tool-effects-in-the-manifest.md) | Tool effects and loop guidance are declared in the manifest; the loop mechanics  | Accepted |  | yes |
| [0111](0111-planner-engages-for-cross-agent-work.md) | The planner engages for cross-agent or parallel work, and hands results downstre | Accepted |  | yes |
| [0112](0112-reminder-text-dates-consent-and-inbox-analysis.md) | Reminder text is summarised, dates are resolved on the user's clock, a clarifyin | Accepted |  |  |
| [0113](0113-finance-forms-accounts-and-due-cycles.md) | The finance agent forms the user's accounts and card due cycles from the evidenc | Accepted |  | yes |
| [0114](0114-memory-is-what-the-owner-confirmed-and-the-prompt-carries.md) | Memory is what the owner confirmed, and context is what the prompt actually carr | Accepted |  | yes |
| [0115](0115-memory-is-a-graph-of-claims.md) | Memory is a graph of claims, and its engine (memris) knows no vocabulary | Accepted |  | yes |
| [0116](0116-health-watch-repairs-then-asks-the-owner.md) | The health watch repairs what it can, then asks the owner — and remembers | Accepted |  | yes |
| [0117](0117-paired-devices-hold-their-own-tokens.md) | Paired devices hold their own tokens; the shared secret is for services | Accepted |  | yes |
| [0118](0118-destructive-tools-are-approved-per-call-by-code.md) | A destructive tool call is approved by code, per call, through the approval queu | Accepted (implemented) | `kernel/governance/plugins/destructive_approval.py`, `plugin_host/manifest.py` | yes |
| [0119](0119-memory-removal-is-reversible-and-reaches-the-agent.md) | Removing something from memory is reversible, and it reaches the agent | Accepted (implemented) | `memory/removal.py`, `memris/graph.py`, `harness_api/memory_routes.py` | yes |
| [0120](0120-settings-the-app-changes-live-on-the-data-volume.md) | Settings the app changes live in one store on the data volume | Accepted |  | yes |
| [0121](0121-finance-reads-trusted-mail-in-full.md) | Finance reads trusted mail in full and answers card questions from records | Accepted |  | yes |
| [0122](0122-the-loop-delivers-or-it-is-not-proven.md) | The loop delivers, or it is not proven | Accepted (implemented) | `services/routines/seeded.py`, `foundation/clock.py`, `config/heartbeats.yaml` | yes |
| [0123](0123-apple-foundation-models-as-a-local-provider.md) | Apple Foundation Models as a local model provider | Proposed (not implemented) |  |  |
| [0123](0123-models-by-capability-not-by-tier.md) | Models are chosen by capability, not by tier size | Proposed (not implemented) |  |  |
| [0125](0125-owner-pii-masking-across-every-guard.md) | Owner-PII masking across every guard | Accepted |  | yes |
| [0126](0126-the-public-tree-carries-no-owner-and-no-deployment.md) | The public tree carries no owner and no deployment | Accepted |  |  |
| [0127](0127-a-turn-the-system-opens.md) | A turn the system opens, and the first-chat welcome | Accepted |  | yes |
| [0128](0128-no-bundled-trace-ui-otlp-to-the-users-backend.md) | No bundled trace UI: OTLP to the user's backend | Accepted |  | yes |
| [0129](0129-harness-services-field-freeze.md) | HarnessServices field freeze + a pinning test | Accepted |  |  |
| [0130](0130-capability-catalogue-stays-closed-for-0x.md) | Capability catalogue stays closed for 0.x | Accepted |  |  |
| [0131](0131-canonical-store-and-rebuildable-projections.md) | Canonical store + rebuildable projection is a standing rule, not a one-off fix | Accepted |  |  |
| [0132](0132-pre-tool-use-intent-record-for-high-risk-actions.md) | A durable PRE_TOOL_USE intent record for high-risk/destructive actions | Accepted |  |  |
| [0133](0133-plugin-manifest-party-field.md) | A `party` field on PluginManifest, distinct from the existing `trust` field | Accepted |  |  |
| [0134](0134-plugin-party-defaults-to-untrusted.md) | `PluginManifest.party` defaults to `untrusted` | Accepted |  | yes |
