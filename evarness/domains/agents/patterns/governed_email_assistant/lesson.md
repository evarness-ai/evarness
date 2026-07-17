# Governed Email Assistant

**Concept.** Agent = Harness + Model. Every deterministic control sits around the
probabilistic core: intent router (non-LLM) -> PII/policy interceptors -> tool +
retriever -> budget-aware context assembly -> LLM -> post-validation interceptors.

**Knobs to try.** retriever.top_k 3 vs 10 (groundedness vs token cost);
shrink params.context_budget_tokens to 500 and watch truncation in context_snapshot.

**Failure lab.** Run the `failure` fixture: the SSN is redacted (redaction_applied)
and policy_check blocks the run before the LLM is ever called (policy_violation).
The scripted "must never appear" response proves the block happened upstream.
