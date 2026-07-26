# Your first node, in five minutes

A node is a class the engine discovers, validates, and runs. You don't
subclass anything to write one — the registry duck-types: a `type_name`, a
pydantic `Config`, port declarations, and a `run` classmethod are the whole
contract. This page builds one, wires it into a graph, and runs it; every
output shown was captured from these exact files.

## The package

Three files:

```text
evarness-shout/
├── pyproject.toml
└── evarness_shout/
    └── __init__.py
```

`pyproject.toml` — the entry point is how the engine finds you; no core
edits, no registration calls in your graph:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "evarness-shout"
version = "0.1.0"
description = "Tutorial plugin: one custom Evarness node"
requires-python = ">=3.10"
dependencies = ["evarness"]

[project.entry-points."evarness.plugins"]
shout = "evarness_shout:setup"
```

`evarness_shout/__init__.py`:

```python
"""Tutorial plugin: a `shout` node the engine discovers via entry points."""

from __future__ import annotations

from pydantic import BaseModel

from evarness.core.registry import NODE_TYPES


class ShoutNode:
    type_name = "shout"
    group = "core"
    doc = "Uppercases its input and appends a suffix. Your first node."
    inputs = {"in": "text"}
    outputs = {"out": "text"}

    class Config(BaseModel):
        suffix: str = "!"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        text = str(inputs.get("in", "")).upper() + cfg.suffix
        ctx.emit("shout_applied", node_id, length=len(text))
        return text


def setup() -> None:
    NODE_TYPES.register(ShoutNode.type_name, ShoutNode)
```

Note what `run` does besides returning a value: it **emits an event**.
`shout_applied` becomes part of the canonical trace — replayable,
digest-covered, and available to invariant contracts (the next tutorial
proves one over it). A node that does something the trace can't see is a
node the product can't reason about.

Note also what the plugin imports: `evarness.core` only. A plugin never
needs a domain's internals.

## Install and check

```bash
pip install evarness -e ./evarness-shout
```

The CLI loads installed plugins before parsing any graph, so your node is
simply *there* — including its config schema, which `validate` enforces
like any packaged node's.

## A graph that uses it

`graph.json`:

```json
{
  "ir_version": 1,
  "id": "shout-demo",
  "name": "Shout demo",
  "nodes": [
    {"id": "n1", "type": "input", "config": {}},
    {"id": "n2", "type": "shout", "config": {"suffix": "!!"}},
    {"id": "n3", "type": "output", "config": {}}
  ],
  "edges": [
    {"from": "n1", "to": "n2"},
    {"from": "n2", "to": "n3"}
  ]
}
```

`fixture.yaml` — no LLM in this graph, so the fixture is just the scenario
and its input:

```yaml
scenario: shout-happy
user_input: "hello evarness"
default_response: {text: "unused - no llm in this graph"}
```

Run it:

```bash
evarness validate graph.json
# OK — graph valid, no lint findings

evarness run graph.json --fixture fixture.yaml
```

```text
  4  node_started           n2     {"type": "shout"}
  5  shout_applied          n2     {"length": 16}
  6  node_finished          n2     {"type": "shout"}
  7  node_started           n3     {"type": "output"}
  8  node_finished          n3     {"type": "output"}
  9  run_finished                  {"total_tokens": 0, "events": 10, "cost_usd": 0.0, "deterministic": true}

STATUS: completed
OUTPUT: HELLO EVARNESS!!
```

Your event is in the stream, between your node's `node_started`/
`node_finished` brackets, exactly where the canonical order contract says
it must be.

## Test it

A node is a classmethod over plain data — the test needs no engine:

```python
from types import SimpleNamespace

from evarness_shout import ShoutNode


def test_shout_uppercases_and_emits():
    events = []
    ctx = SimpleNamespace(emit=lambda name, node_id, **kw: events.append((name, node_id, kw)))
    out = ShoutNode.run("n2", {"in": "hello"}, ShoutNode.Config(suffix="!!"), ctx)
    assert out == "HELLO!!"
    assert events == [("shout_applied", "n2", {"length": 7})]
```

That's the five minutes. The next step is what makes it Evarness rather
than a workflow engine: [prove something about it](tutorial-domain-plugin.md).
