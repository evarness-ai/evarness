# ADR-0111: The planner engages for cross-agent or parallel work, and hands results downstream

Status: Accepted (2026-09-15) — decision 7 of the multi-step loop plan, implemented in the PR that carries this file.
Relates to: multi-step-loop-plan (in the private project log) (decisions 7 and 10), ADR-0045 (dependency-ordered waves), ADR-0110 (tool effects; the loop mechanics are the core's), the owner's rule of 2026-09-13 (vocabulary in YAML).
Supersedes nothing. Narrows the harness doc's "[2] TaskPlanner → for multi-step intents, decomposes into ordered tasks".

## Context

The loop plan's acceptance work found three things about the task planner:

1. **It never engaged for the acceptance query.** `is_multi_step` came from one hardcoded
   regex in `agent/intent_router.py` ("and then", "after that", "first … then", "both",
   "also", "as well as", "followed by"); the user wrote "after this". The regex was core code
   carrying vocabulary, against the owner's rule.
2. **When it did engage, it could not hand data downstream.** `TaskPlanner._llm_plan` yielded
   `TASK: id | action | agent | depends_on`, and the execute stage ran waves in order — but
   every sub-task's query was the *whole* user request (`params={"query": query}`), the
   action text was discarded, and nothing injected an upstream result into a dependent.
   The DAG was ordering only.
3. **For sequential single-agent work it is the wrong tool.** After #482 the loop's
   scratchpad carries each step's Observation to the next; a planner that splits "find the
   dues, then a reminder for each" into two tasks makes the second one blind.

## Decision

1. **The cues live in `config/multi_step.yaml`.** One loader, one compiled pattern, one
   `is_multi_step_query(text)` used by all three classifiers (keyword, LLM, semantic).
   A missing or empty file means no turn is compound: honest degradation, no list in code.
   "after this", "after which", "once that is done", "for each", "for every", "one for each"
   join the shipped cues.
2. **The planner collapses a same-agent chain to one task.** After decomposition, if every
   sub-task resolves to the same agent type *and* no wave holds more than one task (a pure
   chain), the plan becomes the direct single-task plan. Cross-agent plans and parallel
   waves keep their shape — that is the one thing the planner does that the loop cannot.
3. **A sub-task runs its own action, with the request as context, and receives its
   dependencies' results.** `params["query"]` is the sub-task's action; `params["request"]`
   is the user's sentence. Before a dependent task runs, the execute stage renders its query
   as: the overall request, this step, and each dependency's output under its id. Wave 0
   tasks have no dependencies and run as before. Results are kept by sub-task id.

## Consequences

- The loop plan's acceptance query still plans one task (same agent, one chain), and
  "summarize my inbox and check my calendar" plans two (different agents), which is the
  test recorded in decision 7.
- The planner's LLM prompt gains nothing; the parsing keeps the same line format. What
  changes is what a parsed task carries and what the execute stage feeds it.
- Operators tune compound detection in YAML; a false positive costs one planner call that
  collapses to a single task when the sub-tasks share an agent.
