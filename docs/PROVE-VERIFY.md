# Prove & verify — concepts and internals

Companion to [ONBOARDING.md](ONBOARDING.md). Part 1 is the vocabulary — what
each concept *is*, why it exists, and how Evarness resolves it in code. Part 2
walks the implementation (`core/prove.py`, `core/attest.py`) and the live
demos (verified 2026-07-18 against the first-capability-release tree).

---

## Part 1 · What is what, and why

Each entry: **What it is → Why it exists → How Evarness resolves it.**

### Hash (SHA-256)
- **What:** a one-way function that turns any bytes into a fixed 64-hex-char
  fingerprint. Change one bit of input, the output changes completely; finding
  two inputs with the same output is computationally infeasible.
- **Why:** it lets you *name content by its bytes*. If two parties compute the
  same hash, they provably hold the same bytes — no trust needed.
- **How resolved:** `hashlib.sha256` everywhere, always labeled with its
  algorithm (`sha256:…`) so future algorithms can coexist.

### Event / event stream (event sourcing)
- **What:** instead of storing final state, record every step as an immutable
  fact: `{seq, ts, node_id, type, payload}`. The run *is* its event list.
- **Why:** one artifact serves as audit log, replay source, animation feed,
  and — crucially — the evidence that contracts are checked against. State
  can lie about how it was reached; an event stream cannot omit the journey.
- **How resolved:** `Emitter` in `core/executor.py` — every node start/finish,
  every governance decision, every failure is an event. Blocks, pauses, and
  engine faults are **first-class events**, never silent (`policy_violation`,
  `run_paused`, `engine_error`).

### Determinism & the seed
- **What:** a computation is deterministic when the same inputs always produce
  the same outputs. Randomness is made reproducible by seeding the generator.
- **Why:** reproducibility is the foundation of proof. "Run it again and get
  the same answer" turns a claim into a demonstration.
- **How resolved:** `random.Random(graph.params.seed)`, fixture-scripted
  responses only (sim provider), topological order with ties broken by node id
  (`graph.py`), and stable tie-breaking everywhere (e.g. the vector store's
  content-hash jitter). The kernel asks registered `DETERMINISM_INSPECTORS`
  whether anything non-deterministic is present; the run honestly stamps
  `deterministic: true|false` in `run_started`.

### Canonical form (canonicalization, version `c1`)
- **What:** ONE agreed byte-exact representation of data that everyone
  computes identically. For JSON: sorted keys, compact separators, ASCII-only.
- **Why:** hashes compare bytes, not meaning. `{"a":1,"b":2}` and
  `{"b":2,"a":1}` are the same data but different bytes — without a canonical
  form, identical runs would hash differently across machines.
- **How resolved:** `core/trace.py` — drop `ts` (the only nondeterministic
  envelope field), keep `{seq, type, node_id, payload}`, serialize sorted +
  compact + ASCII. The rules are versioned (`c1`) and embedded in every
  digest, so streams normalized under different rules never compare equal
  silently. Deliberately **not** an extension point.

### Trace digest
- **What:** `c1:sha256:<hex>` — the SHA-256 of the canonical event stream.
  The run's *name*.
- **Why:** two runs with the same digest provably produced the same canonical
  behavior. Determinism becomes checkable: same graph + fixture + seed ⇒ same
  digest, on any OS or Python version.
- **How resolved:** `trace_digest()` in `core/trace.py`.

### Hash chain
- **What:** a rolling hash: `h₀ = sha256("")`, `hᵢ = sha256(hᵢ₋₁ ‖ eventᵢ)`.
  The final link is `c1:chain-sha256:<hex>`.
- **Why:** the flat digest names the stream as a *whole*; the chain commits to
  every **prefix**. Truncating, inserting, reordering, or editing any event
  breaks every subsequent link — so an append-only log can be spot-checked
  incrementally and a bundle's stream verified event by event.
- **How resolved:** `chain_digest()` in `core/trace.py`; recomputed by
  `verify`.

### Fixture (scripted simulation)
- **What:** a YAML file that scripts every answer the outside world will give:
  LLM responses, tool results, memory, judge scores, injected faults, attacks.
- **Why:** you cannot prove properties of a system whose inputs change every
  run. A fixture makes scenarios — including hostile ones — exact, repeatable,
  and safe. The fixture is a *deposition, not a brain*: the harness logic runs
  for real against its statements.
- **How resolved:** `domains/agents/sim.py` — first-match-wins substring
  matching, seeded everything, `deterministic = True`. Real providers are
  **refused with remediation** (`providers.py`), never silently simulated.

### Invariant (temporal contract)
- **What:** a declared assertion about what must / must never appear in the
  event stream, and in what order. Four primitives: `never` (with optional
  `after` scoping), `eventually`, `every`, `precedes`.
- **Why:** "a blocked run never reaches the model" as prose is a hope; as a
  contract evaluated on every run, it's a guarantee with evidence. Temporal
  ordering ("X before Y") is exactly what wikis can't enforce and streams can.
- **How resolved:** `core/invariants.py` — YAML definitions resolved
  pattern-local > user overlay > packaged; verdicts carry `evidence_seq`
  pointing at the exact events. Unknown/malformed contracts are **failed
  verdicts, never silent skips**. Verdicts live **outside** the event stream,
  so checking a run never changes its digest (the verdict is *about* the
  evidence, not part of it).

### Proof bundle
- **What:** one portable JSON document: hashed subject, environment, canonical
  evidence (the events), verdicts, reproduction results — and a mandatory
  `not_proven` section.
- **Why:** "trust me, it passed" doesn't travel. A bundle is reviewable by
  someone who wasn't there, on a machine that never ran the code.
- **How resolved:** `prove()` in `core/prove.py` (walkthrough in Part 2).

### Subject pinning
- **What:** hashes inside the bundle that name exactly what was proven: the
  graph, the fixture bytes, the *resolved* contract definitions, the tool
  manifests, the engine + dependency versions.
- **Why:** each pin closes a named loophole — "the graph changed since",
  "the scenario was different", "the contract silently means something else
  now", "a different tool ran". Ask of every hash: *what edit would it catch?*
- **How resolved:** `graph_hash`, `fixture_sha256` (raw bytes),
  `_invariant_defs_hash` (resolved definitions, not just ids), and the
  `SUBJECT_PINNERS` registry so the agents domain pins tool manifests without
  the kernel knowing what a tool is.

### Digital signature (Ed25519 attestation)
- **What:** public-key cryptography: the private key signs bytes; anyone with
  the public key can verify the bytes are unaltered since signing. Ed25519 is
  a modern, fast, small-key signature scheme.
- **Why:** hashes prove *internal* consistency, but an attacker can edit a
  bundle and recompute all its hashes. A signature adds tamper-evidence in
  transit plus a signer identity to pin. It does **not** prove the runs
  happened — the producer computed everything; that trust reduces to the
  signer.
- **How resolved:** `core/attest.py` — the signed payload is the canonical
  JSON of the bundle *minus its `attestation` field* (so the signature can
  ride inside the document it signs). Key auto-created at
  `~/.evarness/keys/proof_ed25519.pem` (0600) on first `--sign`; public key
  embedded in the attestation and written as `.pub` for out-of-band sharing.
  Missing `cryptography` package → clean error naming the `[sign]` extra.

### Offline verification
- **What:** re-deriving every claim in a bundle from the evidence it carries —
  no network, no re-execution, no trust in the producing machine.
- **Why:** evidence you can only check by asking the producer isn't evidence.
- **How resolved:** `verify_proof()` recomputes digests and chains from the
  included events, recomputes the verdict from the scenario rows, and checks
  the signature. Skipped checks report `ok: null` **with a reason** — never
  silently absent.

---

## Part 2 · The internals

### `prove()` — building the bundle (`prove.py:123-230`)

Per scenario `(name, fixture_yaml_text)`:

1. **Run once**, digest the trace. `fixture_sha256` hashes the raw YAML bytes.
2. **Run a second time, compare digests** (`prove.py:149-155`):
   `reproduced = trace_digest(second.events) == digest`. Reproducibility is
   **demonstrated, not asserted**. Honest sub-cases:
   - `paused` → no second run, `reproduced: null`, `not_proven` note says
     "prove the resumed branches by passing --approve";
   - `deterministic: false` → digest identifies the trace but reproduction is
     not expected (noted);
   - otherwise reproduction is checked for real.
3. **Record verdicts + full canonical events** (that inclusion is what makes
   offline verification possible).
4. **Judge nothing about run status** (`prove.py:14-17`): a failure-lab
   fixture that ends `blocked` is a governance *success*. Only contracts
   define "correct"; status is recorded, not judged.

Verdict math (`_verdict()` in `prove.py` — shared verbatim with
`verify_proof`, so producer and reviewer can never disagree):

```
failed = (not declared) or invariants_pass is False or reproduced is False
ok     = false if failed else (null if any scenario paused else true)
```

Three honesty rules live in that helper (E8):

- **nothing-asserted**: no declared invariants ⇒ `ok: false` with a note. An
  empty claim must never verify as a passing proof.
- **pending**: nothing failed but a scenario paused ⇒ `ok: null`. Zero
  invariants evaluated and zero reproductions attempted is not a proof, and
  it is not a failure either — pausing is designed behavior. The CLI prints
  `PROOF: PENDING` and exits 1; JUnit gets a failing `proof complete` case,
  SARIF a `proof-pending` warning, the HTML badge reads PROOF PENDING.
- **no vacuous truth**: `verdict.invariants_pass` and `verdict.reproduced`
  are `null` (not `true`) when nothing was evaluated/attempted.

Bundle top-level keys: `proof_version` (`p2`), `generated_at`, `engine`,
`environment`, `subject`, `scenarios`, `verdict`, `not_proven`
(+ `attestation` when signed).

### `verify_proof()` — offline re-checking (`prove.py:236-349`)

Emits a typed checklist; `ok` is `true` / `false` / `null`
(= skipped-with-reason). Families:

1. **digest recomputes** — `trace_digest` over included events vs claim;
2. **event chain recomputes** — `chain_digest` likewise;
3. **event count matches**;
4. **verdict consistent with scenario rows** — recompute
   `invariants_pass`/`reproduced`/`ok` from the rows; catches a bundle whose
   summary lies about its own details;
5. **signature** — validated if present; `--require-signature` turns
   "unsigned" from a note into a failure.

Final: `ok = all(c["ok"] is not False)` — skips don't fail, contradictions do.

### CI projections (rest of `prove.py`)

Renderers project **verdicts** (never raw traces — those are
`io/exporters.py`):

- **JUnit** — one testcase per invariant + one for digest reproduction; a
  graph with no invariants renders a **failing** case (nothing-asserted must
  break CI).
- **SARIF 2.1.0** — invariants become rules; violations carry `evidence_seq`
  and the trace digest so findings trace to exact canonical evidence;
  nothing-asserted is a warning result, not an empty (passing) log.
- **HTML** — self-contained report; badge tri-state says it all:
  **PROOF HOLDS / PROOF FAILED / NOTHING ASSERTED**.

### Live demos (all reproduced 2026-07-18)

| Demo | Action | Result |
|---|---|---|
| Tamper | flip one event `approval_granted` → `approval_skipped` in a real bundle | `✗ digest recomputes`, `✗ event chain recomputes` — VERIFY FAILED, exit 1 |
| Sign + pin | `prove --sign` (key auto-created), `verify --pubkey <pub> --require-signature` | all checks green incl. `ed25519 signature valid (pinned key)` |
| Strip | delete the `attestation` block (downgrade attack) | integrity checks still pass (content unmodified) but `✗ signature — bundle is unsigned (required)` — FAILED |

Commands:

```bash
evarness prove approval_gated_send --approve n3=approve --sign -o proof.json
evarness verify proof.json --pubkey "$(cat ~/.evarness/keys/proof_ed25519.pub)" --require-signature
```

CI consuming third-party bundles should **always** pass
`--require-signature` — it is the difference between "internally consistent"
and "provably from the signer."

### The three design rules to drill

1. **Demonstrate, don't assert** — the second run; the recomputed digests.
2. **Every hash closes a named loophole** — graph, fixture bytes, *resolved*
   contract definitions, tool manifests. Ask: what edit would this catch?
3. **Absence is loud** — nothing-asserted fails, skips carry reasons,
   unsigned is flagged, `not_proven` is mandatory.
