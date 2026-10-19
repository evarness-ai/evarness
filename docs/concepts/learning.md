# Learning

Evarness watches its own performance and writes down what it would change. By default it
**measures and proposes**. It **applies nothing** until you turn a flag on, and what it
applies goes through the same governance kernel as every other call. This page is the loop
end to end: what it reads, what it writes, which jobs run by default, which capabilities
are off, and where to look.

## What it reads

Every turn leaves **signals** in the learning store (`learning.db` under the data
directory): the intent router's confidence, whether the response had errors, tool failures
and the failure streak, task outcomes (`task_completed`), whether the owner corrected the
previous answer (`user_correction`), the escalation shadow (which tier would have been
chosen and whether a bigger model was needed), and surface feedback (useful or not useful on
anything the harness proactively surfaced). Signals are measured outcomes, not model
guesses; the two judgements that need a model (the escalation judge and the correction
judge) are off by default and governed when on.

## What runs by default

Four scheduled jobs, declared in `config/heartbeats.yaml`, run in the Evarness API process:

| Job | Every | What it does | What it writes |
|---|---|---|---|
| `learning_tick` | 15 min | Reads the strategies in `config/learning/strategies.yaml`. Each strategy watches one metric; when the baseline crosses its trigger, the tick proposes an experiment (a config change with a rollback) and later evaluates it against `min_improvement_pct` over its window. | Experiments in the learning store, status `proposed`, `running`, `kept` or `discarded` |
| `escalation_priors_tick` | 1 h | Mines the escalation shadow: which intents kept needing a bigger model. Recommends a start tier per intent. | Recommendations; applied to the router only when priors are enabled |
| `crystallize_tick` | 6 h | Finds (intent, agent) pairs handled well, repeatedly, with a low correction rate, and packages each as a skill proposal. With a synthesizer enabled, a governed model call writes the skill's content; without one, a scaffold. | `config/skills/auto/<slug>/proposal.yaml`, quarantined: the skill registry excludes that directory |
| `experiment_remeasure_tick` | 6 h | Re-reads the current value of each promoted experiment's target and keeps or discards it past its window. | Only experiments a human explicitly promoted |

The strategies file is the whole policy for experiments. Each entry names a metric, a
baseline sample size, a trigger, a variant (config changes and their rollback) and an
evaluation window. Two ship: raise the intent router's confidence floor when confidence is
low, and switch the curator to the advanced tier when the response error rate is high.

## What is off until you turn it on

| Capability | Default | Setting | What changes when on |
|---|---|---|---|
| Escalation judge | off | `EVARNESS_CURATOR_ESCALATION` | The curator asks a judge whether a turn deserves a bigger model. It starts in `shadow` (`EVARNESS_CURATOR_ESCALATION_MODE`), recording the verdict and changing nothing; `enforce` escalates, at most `max_escalations` per turn (`config/escalation.yaml`). |
| Learned start-tier priors | off | `EVARNESS_CURATOR_ESCALATION_PRIORS`, or `priors.enabled` in `config/escalation.yaml` | The hourly recommendations are applied: an intent that kept escalating starts on the higher tier. |
| Cloud escalation | off | `EVARNESS_CURATOR_ESCALATION_CLOUD` | The judge may escalate to the cloud tier. The egress gate still refuses personal and secret data; policy overrides routing. |
| Routine reflection | off | `EVARNESS_ROUTINE_REFLECTION` | A 12-hour job proposes changes to failing or stale routines, into the owner's approval queue. |
| Sandbox pre-flight | off | `EVARNESS_SANDBOX_PREFLIGHT` | Before a promoted experiment is re-measured live, a frozen workload is replayed through an isolated runtime under baseline and variant, and the verdict is stamped on the experiment. Builds a runtime and runs local models. |
| Crystallize pre-flight | off | `EVARNESS_CRYSTALLIZE_PREFLIGHT` | The same replay gate for skill proposals before they are written. |
| Behavior miner | off | `EVARNESS_BEHAVIOR_MINER` | The digital twin's first layer: a local-tier pass over recent turns proposes behaviors for your approval (`evarness behaviors`). |
| Intention rollup | off | `EVARNESS_INTENTION_ROLLUP` | The third layer: rolls behaviors up into goals, proposed for approval (`evarness intentions`). |
| Learning analyst | off | `EVARNESS_LEARNING_ANALYST` | A local-tier agent reads the learning telemetry and writes a report every 12 hours. It never acts. |
| Correction judge | off | `EVARNESS_LEARNING_CORRECTION_JUDGE` | A governed model call on turns that look like corrections, to record `user_correction` more precisely. |
| Agent self-management | off | `EVARNESS_AGENT_SELF_MANAGEMENT` | The agent gets tools to act on its own memory and settings, in the same governed pool. |

The three digital-twin flags can also be flipped live from the console's Self-Learning
screen, without a restart; the environment value is what a restart reverts to. Every
setting above is in the settings catalog with its owner and scope
([environment variables](../reference/environment-variables.md)).

## Promoting what it proposes

Nothing moves from proposal to behaviour on its own.

- **An experiment** is kept or discarded by its evaluation, but a recommendation-derived
  experiment runs only after a human promotes it; `evarness learning recommendations` lists
  them and `evarness learning preview` shows what a variant would change.
- **A skill proposal** is a directory under `config/skills/auto/`. Read it, edit it, and
  move it into `config/skills/` yourself; until then the registry does not see it.
- **A behavior or an intention** waits in the approval list (`evarness behaviors`,
  `evarness intentions`, or the console) until you accept or reject it.
- **A routine reflection** is an item in the approval queue like any other owner decision.

## Where to look

- `evarness learning status`: proposals awaiting and accepted, signals recorded and
  dropped, the escalation precision, the latest analysis.
- `evarness learning intelligence`: the measured outcome cells behind the recommendations.
- `evarness learning signals`: the raw signal kinds and counts.
- The console's **Self-Learning** screen: the same numbers, the live toggles, and the
  experiment list.
- The audit ledger: every governed call the loop makes (the judges, the synthesizer, the
  analyst) is a row like any other, with the agent that made it.

## Why it is built this way

Shadow, then validate, then enforce is the shape of the whole harness, and the learning loop
follows it: a recommendation is recorded before it is applied, a variant is replayed in a
sandbox before it runs live, a skill is quarantined before it is registered. The reasons are
in the decision log: the curator-driven escalation judge
([ADR-0068](../adr/0068-curator-escalation-judge.md)), the sandbox replay pre-flight
([ADR-0070](../adr/0070-sandbox-replay-eval-for-experiments.md)), and the experiment console
([ADR-0083](../adr/0083-learning-experiment-console-hot-toggle-no-restart.md)).
