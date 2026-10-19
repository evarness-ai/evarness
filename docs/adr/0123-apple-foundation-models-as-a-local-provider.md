# ADR-0123: Apple Foundation Models as a local model provider

Status: Proposed (2026-09-29). Measured on 2026-09-28 in the bake-off recorded in

**Status pass (2026-10-18):** still Proposed; not implemented. no `apple` provider exists; the owner said to wait

`docs/architecture/model-bakeoff-apple-fm.md` (#708). The owner asked for the decision
and its evidence to be written down now, and for the build to wait.
Relates to: cloud-trial plan decision 4 (FoundationModels is not the main brain; it goes
into the bake-off), ADR-0002 (privacy-aware tiers), ADR-0068 (curator escalation,
shadow-first), ADR-0116 (health watch), ADR-0120 (settings the app changes).
Supersedes nothing.

## Context

With macOS 27 and iOS 27, Apple ships an on-device language model to every Apple
Intelligence device through the `FoundationModels` framework (`SystemLanguageModel`).
It has an 8,192-token window, guided generation (`@Generable` / `DynamicGenerationSchema`),
greedy sampling, `tokenCount`, `prewarm()` and typed errors for context overflow,
guardrails and refusals. It costs nothing to run, it keeps data on the device, and it
does not have to be loaded first.

IRIS runs its router (`llama3.2:3b`) and tier 1 (`granite4.2:3b`) on the Mac's Ollama,
reached from the VM over the tailnet. The owner asked whether Apple's model can take
those two jobs, as a step toward running parts of IRIS on the iPhone.

The framework is Swift-only and Apple-only. The VM (Linux) cannot call it, and IRIS's
providers speak Ollama or OpenAI-style HTTP.

## Measurements (2026-09-28)

Everything below ran on the owner's Mac (macOS 27.2) and iPhone 16 Pro (iOS 27.0). No
private text left either device: judging and rating were local.

### How it was measured

- **Bridge.** A scratch Swift server on 127.0.0.1 exposed `/v1/chat/completions` over
  `SystemLanguageModel.default`, with greedy decoding at temperature ≤ 0.1 and an
  optional guided enum for the router. IRIS reached it through the existing `lmstudio`
  provider, with no harness code changed. That setup exposed the `num_ctx` bug fixed in
  #711.
- **Router.** The 131 labelled messages of the Needle and Laya tests went through IRIS's
  real chain (`_build_intent_classifier`, `IRIS_INTENT_ROUTER_SEMANTIC=1`: MiniLM, then
  keywords, then the LLM), and through the LLM alone.
- **Tier 1.** 251 real VM tier-1 calls (09-19 to 09-28) were replayed with identical
  inputs on `granite4.2:3b` and on Apple's model. 131 were logged whole. The log's
  8,192-character middle cut had removed the tool menu from 120 ReAct prompts: 67 of
  those were rebuilt from code as close copies and 50 were dropped. #707 now lets a
  deployment log whole prompts.
- **Quality.** The owner rated 20 pairs blind, with A/B shuffled. A local judge
  (gemma4:12b) rated all 198 non-identical pairs in both orders.
- **Devices.** One Swift benchmark with synthetic prompts at IRIS's real sizes ran on
  both the Mac and the iPhone (58 calls each).

### Router (131 messages)

| Setup | Intents | By agent | p50 | p90 |
|---|---|---|---|---|
| llama3.2 in the chain (production) | 112 | 117 | 42 ms | 168 ms |
| llama3.2 alone | 103 | 110 | 108 ms | 119 ms |
| Apple alone | 115 | 119 | 762 ms | 1,609 ms |
| Apple alone, guided enum | 111 | 115 | 774 ms | 895 ms |
| **Apple in the chain** | **117** | **120** | 43 ms | 833 ms |
| Apple in the chain, guided enum | 116 | 119 | 42 ms | 828 ms |

- Apple fixed 5 messages the chain gets wrong today and broke none: how-to questions now
  go to `help`, and chat that only mentions a domain now goes to `general`.
- 356 calls: 0 errors, 0 guardrail blocks, 0 overflows. The router prompt is about 842
  Apple tokens.
- Guided generation cost 4 intents, so it is not used.
- An Apple call has no prefix cache and takes about 750 ms on the Mac. `prewarm()` with
  the router instructions cuts that to 476 ms. For comparison, the VM's live llama router
  calls to the Mac run at p50 0.76 s and p90 4.26 s (cold loads).

### Tier 1

- **Fit.** All 251 prompts fit the 8,192 window. Apple-token p50 is 1,245 as logged;
  with the missing middle added back, p90 is about 5,000. About 3 finance calls would
  overflow once a 1,024-token reply is reserved.
- **Behaviour** (granite4.2 vs Apple):

  | Check | granite4.2 | Apple |
  |---|---|---|
  | Errors / overflows (198) | 0 | 0 |
  | Curator returns valid JSON (110) | 104 | 106 |
  | ReAct: same step type as the VM (67) | 54 | 55 |
  | ReAct: same tool as the VM (11) | 5 | 5 |
  | Curator p50 (Mac) | 1.11 s | 1.96 s |
  | ReAct p50 / p90 (Mac) | 3.30 / 5.63 s | 3.59 / 7.71 s |

- **Curator decisions (110):** the models disagree sharply.

  | Action | VM granite4 | granite4.2 | Apple |
  |---|---|---|---|
  | accept | 75 | 38 | 33 |
  | clarify | 9 | 53 | 4 |
  | escalate | 3 | 1 | 52 |
  | reroute | 5 | 10 | 17 |

- **Quality.** The owner's blind rating came out Apple 10, granite 9, tie 1 (curator 5–4–1,
  agent steps 5–5). The judge came out granite 76, Apple 71, tie 51. The judge matched the
  owner on only 7 of 20 pairs and picked granite on every curator pair the owner rated,
  so it is not used as evidence. The verdict is parity, on a sample of 20.

### Mac vs iPhone (same synthetic prompts, time to first token p50)

| Prompt | Mac | iPhone 16 Pro | iPhone total (p50) |
|---|---|---|---|
| Router, 456 tokens | 0.51 s | 1.30 s | 1.31 s |
| Router, prewarmed | 0.42 s | 0.56 s | 0.60 s |
| Tier 1, 1.2K | 1.38 s | 2.22 s | 5.25 s |
| Tier 1, 2.2K | 2.20 s | 3.67 s | 6.20 s |
| Tier 1, 3.5K (ReAct size) | 3.34 s | 6.33 s | 12.03 s |

- The phone reached thermal state `serious` within about 4 minutes of continuous
  generation.
- On the same prompts with greedy decoding, the phone wrote much longer answers (500–680
  characters against 142 on the Mac). The two OSes probably ship different model versions.

## Decision

1. **Add a core `apple` model provider.** It is a mechanism, so it belongs in core. A tier
   selects it the way it selects `ollama` or `lmstudio`. The core speaks HTTP to a small
   local **FM bridge service** on the Mac node:
   - launchd-managed and served to the tailnet with `tailscale serve`, like Ollama;
   - OpenAI-style `/v1/chat/completions` plus `/health`;
   - it reports the OS build and model availability, so every answer can be traced to a
     model version.

   The bridge never calls out, and it prewarms the router session.
2. **Router first, in shadow mode.** For one week Apple routes every turn beside
   llama3.2, and each disagreement is logged: message hash, both intents, latencies. The
   production route stays llama's. Apple becomes the router only if:
   - its disagreements, rated by the owner, favour it;
   - its p90 on the VM stays under 1.5 s;
   - fewer than 1% of its calls fail.

   llama3.2 remains the fallback whenever the bridge is unreachable.
3. **Tier 1 is decided per job, not all at once**, and not before the exact-prompt rerun
   (the capture window with `IRIS_LOG_PROMPT_CAP` ends 2026-10-06). The response curator
   stays on its current model until a labelled curator set exists (see 4). Apple's 52
   escalations in 110 would move that load to tier 2.
4. **The curator gets a ground truth first.** Both candidate models disagree with the old
   curator on half the answers, in opposite directions: granite4.2 clarifies, Apple
   escalates. A small owner-labelled set of curator cases comes before any model change
   there, and it also answers whether the 09-28 switch to granite4.2 regressed the curator.
5. **The iPhone is out of scope here.** The phone suits single short steps (routing,
   short extractions) at about 1 s, not ReAct turns (12 s a step, thermal throttling). It
   is reconsidered only with the native-app track of the mobile plan, where the app is in
   the foreground.
6. **Every OS update re-runs the eval.** Apple's model version cannot be pinned, and it
   changes with macOS and iOS updates. The bridge reports the OS build. When the build
   changes, the router set (131) and the tier-1 replay set run again, and the provider
   falls back to llama if accuracy drops more than 3 intents below the last accepted run.

## Alternatives considered

- **Keep llama3.2 as the router.** It is simpler, but it measured 5 intents worse, with
  cold-load p90s of over 4 s on the VM.
- **Replace tier 1 wholesale now.** Rejected: quality is only at parity, the curator's
  behaviour changes sharply, and 50 of the ReAct prompts could not be measured.
- **Run IRIS's harness on the iPhone.** Rejected again, as in the cloud-trial plan: no
  background execution, a suspended phone cannot serve the VM, and ReAct latency and heat
  are too high.
- **Call FoundationModels from Python (PyObjC) inside IRIS.** Rejected: it ties the
  harness process to macOS and to a bridging layer. A separate service keeps the VM
  harness platform-independent and keeps its HTTP contract testable.
- **Guided generation for routing.** Measured worse (−4 intents), so not used.

## Consequences

- **Privacy.** Router and tier-1 text stays on the owner's Mac, as it does with Ollama
  today. No new data path to a cloud. The provider carries the same data label as the
  Mac's Ollama (`private` allowed).
- **Availability.** The Mac must be awake, as for Ollama today. The fallback to llama3.2
  applies when the bridge is down, and the same Mac sleep also takes Ollama down, so
  health watch (ADR-0116) gains a `fm_bridge` check.
- **Operations.** The bridge is one more service on the Mac node, with its own launchd
  job and a tailnet port. Model updates arrive with OS updates, outside IRIS's control,
  hence decision 6.
- **Cost.** None: no Azure spend and no extra model download.
- **Code.** A provider entry and client path in `llm/`; a Swift service, which is either
  its own small repo or `deploy/mac-node/`; a shadow-routing mode in the intent router;
  settings for the provider and shadow flag (ADR-0120 catalog); tests against a fake
  bridge.

## Plan (not started)

1. Bridge service: launchd, tailscale serve, `/health` with the OS build, prewarm, logging
   without message text.
2. `apple` provider plus a fake-bridge contract test.
3. Shadow routing for one week, then the owner reviews the disagreements.
4. Router switch, or not, by the criteria in decision 2.
5. After 2026-10-06: the exact-prompt tier-1 rerun and the curator labelled set (decisions
   3 and 4).
6. An OS-update re-eval job (decision 6).
