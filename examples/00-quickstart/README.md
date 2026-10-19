# 00 · Quickstart

From install to a first governed turn: check the machine, run the email demo, then
drive Evarness from Python.

## 1. Check the machine

```bash
evarness doctor
```

A preflight: Python, platform, RAM, disk, the Evarness home, the vault key, Ollama and the
starter models. It ends with a verdict -- **ready**, **demo only** (the synthetic demo
runs; real use needs a model server) or **not ready** -- and what to fix. `evarness doctor
--fix` applies the safe fixes (pulls missing models, creates a vault key); `--json`
prints the report for a script.

## 2. Run the email demo

```bash
evarness email demo
```

Evarness on a synthetic mailbox of 200 emails, in its own home (`~/.evarness-demo`, never your
profile), on a scripted demo model: no model server, no credentials, no network. It
fetches, judges every email into Needs reply / Bill / Event / FYI / Unsure, walks email
setup's label preview and approval for the demo's own account, writes the labels,
and prints the first digest and **what Evarness just did** -- model calls, audit rows,
network connections attempted (0). Run it again: nothing is fetched or judged twice.
`--reset` starts over.

## 3. A first governed turn from Python

```bash
python examples/00-quickstart/first_turn.py
```

`first_turn.py` builds the same Evarness the `evarness` command runs, with
`evarness.testing.harness`, in a throwaway home on a scripted model, asks one
question and prints the answer and the audit rows that turn wrote -- two model calls
(the router's, then the answer), each checked for its data's classification and where
it may go before it ran:

```
You:  What can you help me with?
Evarness: I can sort your email, keep your to-dos and answer from your own documents.
      (intent general, agent system)

8 audit rows for this turn:
  pre_turn       data_classifier        allow
  pre_classify   data_classifier        allow
  pre_llm_call   redaction_filter       allow
  pre_llm_call   egress_gate            allow
  pre_classify   data_classifier        allow
  pre_llm_call   redaction_filter       allow
  pre_llm_call   egress_gate            allow
  pre_response   response_safety        allow

Every model call and every answer audited: yes
```

## Run the checks

```bash
pytest examples/00-quickstart -q
```

Expected output: `3 passed` in about 26 s (9 s with `--no-cov`). The test runs `evarness doctor --json` and
`evarness email demo` as child processes, the way you type them, in a temporary home (the
doctor's model-server probe goes to a closed local port, so its verdict is not
"ready"), then the first turn.

## Try changing

Put a personal detail in the question and watch where the model calls were allowed to
go. In `first_turn.py`, set

```python
QUESTION = "My email is sam@example.com. What can you help me with?"
```

and print each row's classification and tier as well:

```python
print(f"  {row.hook_point:<14} {row.plugin:<22} {row.decision:<6} {row.classification} {row.tier}")
```

Run `python examples/00-quickstart/first_turn.py` again: the `pre_llm_call` rows now say
`personal` and name a local tier (`tier_1` for the router, `tier_2` for the answer),
where the original question said `public`. The address was classified before any model
saw it, and the egress gate allowed it only to local models.

## Next

[`01-deterministic-handler`](../01-deterministic-handler/): answer a question with no
model at all, governed like a generated answer. Background:
[Try the demo](../../docs/getting-started/demo.md) and
[Architecture](../../docs/concepts/architecture.md).
