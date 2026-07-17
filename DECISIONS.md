# Evarness — decision log

Design decisions, recorded as they're made. The log is part of the product:
every rule the engine enforces should trace to a reasoned entry here.

| # | Decision | Why |
|---|---|---|
| E1 | **The first release is the assurance spine, simulation-only** — canonical traces, invariant contracts, proof bundles, offline verification — not the full platform (real providers, sandboxed real tools, adapters, visual builder all exist on the roadmap as separate releases). | Everything else stacks on the spine: if traces aren't canonical and contracts aren't checkable, no later capability can be *proven*, only demonstrated. Shipping it alone, narrowly, means the project's first public claim is its most defensible one — and each later capability arrives with the machinery to prove itself already in place. |
| E2 | **Ungraduated capabilities refuse, they don't degrade.** A graph naming a real provider (`anthropic:…`, `ollama:…`) gets a `ProviderError` naming the sim alternative; a `mode: real` tool gets a traced governance block (`policy_violation`) with remediation. Both are tested. | A silent fallback to simulation would make the trace lie about what ran — the exact failure mode this project exists to prevent. An honest refusal also documents the roadmap in the error message itself. The node config keeps its `sandbox`/`egress` knobs so graphs written today parse unchanged when real execution arrives; the run refuses before reading them. |
| E3 | **The canonical digest is a portable contract, not an implementation detail.** `c1:sha256:…` over the canonical event stream (wall-clock envelope stripped, byte-stable serialization). Verified: the digest for the same graph+fixture+seed is identical across macOS/Python 3.14 and Linux/Python 3.10, and identical between a development checkout and the installed wheel. Anything that changes canonical output bumps the `c1` version. | Reproducibility claims are only worth something if they survive the machine boundary — a digest that differs across OS or Python version would reduce "reproducible" to "reproducible on my laptop". The versioned prefix makes canonicalization changes explicit and comparable instead of silent breakage. |
| E4 | **Invariant verdicts live outside the event stream.** Checking a run never alters its digest; a bundle with no declared invariants verifies as "nothing asserted", exit-code-visible. | Evidence and judgment must not contaminate each other: if verification wrote into the trace, verifying a run would change its identity. "Nothing asserted" is kept loud because a green checkmark over zero contracts is the most dangerous kind of pass. |
| E5 | **Tests never touch the developer's home.** The suite pins `EVARNESS_DB` to a temp dir and clears every `EVARNESS_*` overlay before the first import; `evarness.store` resolves its data dir at import time, which is why the isolation lives in `conftest.py` rather than a fixture. | A test suite that reads the developer's own overlays is non-deterministic in exactly the way this project tells its users not to be. |

Deliberate pre-1.0 simplifications, documented rather than hidden: stdlib
`sqlite3` (no ORM) for the activity log; `len//4` token approximation (a real
tokenizer would add a heavy dependency for no contract-relevant gain — token
counts inform budgets, they are not part of the determinism contract).
