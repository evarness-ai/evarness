# A domain plugin, without touching core

The [first tutorial](tutorial-custom-node.md) registered a node and emitted
a custom event. This one closes the loop that makes the node *provable*: a
contract over your own event vocabulary, a proof bundle, and an offline
verification — still without modifying a line of Evarness. Every output
shown was captured from the tutorial package.

## Declare a contract over your own event

Drop `invariants.yaml` next to `graph.json` — pattern-local contracts
travel with the graph and load automatically:

```yaml
invariants:
  shout-before-output:
    title: The shout happened
    description: Every run emits shout_applied before it finishes.
    assert:
      eventually:
        type: shout_applied
```

The graph opts in (contracts are declared, never ambient):

```json
"params": {"invariants": ["shout-before-output"]}
```

`shout_applied` is nothing special to the kernel — it's a name your domain
made meaningful. The contract machinery (`never` / `eventually` / `every` /
`precedes`, `where` clauses) works over any vocabulary a domain emits.
That's the extension thesis in one sentence: **nodes and events are cheap
to add; determinism, tracing, proof, and verification come with them.**

## Prove it, verify it

```bash
evarness prove graph.json --fixture fixture.yaml -o proof.json
```

```text
  fixture          completed  invariants 1✓/0✗    reproduced      c1:sha256:89b45c4909c1d9…
PROOF: HOLDS — 1 scenario(s), invariants_pass=True, reproduced=True
wrote proof.json
```

`prove` ran your scenario twice and compared canonical digests — your
custom node just inherited the reproducibility demonstration. Then:

```bash
evarness verify proof.json
```

```text
  ✓ event chain recomputes [fixture] — c1:chain-sha256:78977bc01c3be821…
  ✓ event count matches [fixture] — 10 events
  ✓ verdict consistent with scenario rows — ok=True invariants_pass=True reproduced=True
  - signature — bundle is unsigned — integrity checks above still hold
VERIFY: OK — bundle is internally consistent
```

A bundle carrying evidence about *your* node, checkable on a machine that
has never seen your code run.

## The full seam surface

A node plus a contract is the small end of a domain. The registries in
`evarness.core.registry` are the whole list of what a domain can
contribute — each one is how the packaged agents domain wires itself in,
so `evarness/domains/agents/` is a complete worked example:

| Seam | What your domain supplies |
| --- | --- |
| `NODE_TYPES` / `register_node` | node types |
| `register_lint_rule` | policy lints in your vocabulary (the kernel checks structure only) |
| `register_context_extension` | your per-run state object, namespaced under `ctx.ext` |
| `register_contract_source` | a packaged invariant library |
| `register_determinism_inspector` | "does this graph leave simulation?" |
| `register_subject_pinner` | extra artifacts pinned into proof subjects |
| `set_provider_factory` / `set_environment_loader` | how provider specs and scenario documents resolve |

Two boundaries to respect, both enforced by tests in this repo:

- **Core never imports you** — you register, it calls. If your design needs
  a core edit, that's a conversation (an issue), not a patch.
- **The trace must not lie** — emit what happened, when it happened. An
  event your node didn't earn is worse than no event.

The agents domain is currently the only in-tree domain; a second,
non-agent domain is the architecture's declared next test (see the honesty
ledger in [ARCHITECTURE.md](architecture.md)). If you're building one,
open an issue early — the seams above are exactly what community feedback
should pressure-test.
