# Playground scenarios

YAML test cases for the Evarness harness. Each scenario sends a message through the
real runtime under a declared set of flags and asserts on the outcome — which
intent it routed to, which intercept answered, what tools fired, and whether the
guardrails held. The engine lives in `src/evarness/playground/`; surfaces are
`evarness playground`, the `/playground` API, and the web Playground screen.

## Why this exists

- **Regression net for refactors.** Snapshot a suite's outcomes before a change
  (`evarness playground run <suite> -b before.json`), then `diff` after
  (`evarness playground diff <suite> -b before.json`). It flags exactly the turns
  whose behavior moved — the safety belt for the bootstrap decomposition.
- **Living documentation.** A scenario is a readable statement of how the
  harness is supposed to behave ("insurance dues must not web-search").
- **Adopter test bench.** Anyone extending Evarness writes a YAML case, no Python.

## Files

- `core-deterministic.yaml` — intercepts that answer without the LLM or external
  data (time/date). Runs in CI; the committed regression net.
- `routing-breadth.yaml` — natural phrasings vs the fast handlers + action-verb
  negatives (escalation gate). Needs live Ollama for the agent-loop cases.
- `injection-pressure.yaml` — injection suffixes on normal requests; asserts the
  safe path holds (no PII leak, no web search). Needs live Ollama for one case.
- `intercepts-live.yaml.example` — template for flag-gated intercepts that read
  local stores (finance/dues/portfolio/brief). Copy, seed data, run locally.

Playground runtimes are built with a write-safety floor
(`EVARNESS_DISABLE_EXTERNAL_WRITES=1`, `EVARNESS_CALENDAR_APPLE_WRITE=0`), so scenarios
never write into the real Apple/Google calendar; a suite that wants live
write-back opts back in via its `env` block. One caveat remains: the dev
`.env` still fills any flag a suite leaves unset, so a suite that A/B-tests a
flag must force it explicitly. See
`docs/experiments/2026-07-05-deterministic-intercept-campaign.md`.

## Writing a scenario

```yaml
name: my-suite
env: { EVARNESS_SOME_FLAG: "1" }        # build-time flags for the whole suite
scenarios:
  - name: a-case
    message: "what the user types"
    setup_messages: ["prior turn to establish state"]   # optional
    expect:
      handler: dues_request          # intercept phase name, or "" for the agent loop
      intent: finance
      sources_exclude: [research]    # must NOT have web-searched
      response_contains: ["due"]
      no_pii_leak: true              # no email / prompt marker in the reply
```

Only the `expect` fields you set are asserted. Prefer stable signals
(intent/handler/sources/guardrails) over exact response text, which varies under
a live LLM.
