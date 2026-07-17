"""Canonical trace normalization — the determinism contract, published.

An engine event is ``{seq, ts, node_id, type, payload}``. The determinism
contract ("same graph + fixture + seed => identical event stream") holds over
the CANONICAL form of the trace, not over raw bytes: ``ts`` is wall-clock time,
so two runs can never be byte-identical. Everything else — event order, types,
node ids, and full payloads — must reproduce exactly on a deterministic run.

Normalization rules, version ``c1``. These are a published contract: anything
that changes the output of ``canonical_json()`` for an existing trace is a
breaking change and MUST bump ``CANONICALIZATION_VERSION`` (digests embed the
version so streams normalized under different rules never compare equal
silently).

1. **Envelope**: each event keeps exactly ``seq``, ``type``, ``node_id``,
   ``payload``. ``ts`` (the only nondeterministic envelope field) is dropped.
   The run id is not part of any event and is likewise excluded.
2. **Payloads**: kept whole and unmodified — no field inside ``payload`` is
   filtered. Nondeterministic *content* (a real provider's answer, a live
   tool's results) is not scrubbed; the run honestly declares itself in
   ``run_started.payload.deterministic``, and digest equality is only expected
   when that flag is true.
3. **Serialization**: JSON with sorted keys, compact separators
   (``,`` / ``:``), and ASCII-escaped non-ASCII — byte-stable across platforms
   and Python versions.
4. **Order**: emission order (``seq`` ascending) is preserved, never re-sorted.

Deliberately NOT an extension point: user-configurable normalization would make
digests incomparable between installations, and the entire value of a canonical
form is that everyone computes the same one.
"""

from __future__ import annotations

import hashlib
import json

CANONICALIZATION_VERSION = "c1"

# The envelope fields an event keeps (rule 1); everything else — today only
# ``ts`` — is dropped.
CANONICAL_ENVELOPE_FIELDS = ("seq", "type", "node_id", "payload")


def canonical_event(event: dict) -> dict:
    """One event reduced to its canonical envelope (rule 1)."""
    return {k: event.get(k) for k in CANONICAL_ENVELOPE_FIELDS}


def canonical_trace(events: list[dict]) -> list[dict]:
    """The canonical form of an event stream (rules 1 and 4)."""
    return [canonical_event(e) for e in events]


def canonical_json(events: list[dict]) -> str:
    """Byte-stable serialization of the canonical trace (rule 3)."""
    return json.dumps(
        canonical_trace(events), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def trace_digest(events: list[dict]) -> str:
    """Digest of the canonical trace: ``c1:sha256:<hex>``.

    Two runs of the same (graph, fixture, seed, engine version) produce the
    same digest when the run is deterministic (``run_started`` payload says
    ``deterministic: true``). For real-provider/real-tool runs the digest still
    identifies the trace but is not expected to reproduce.
    """
    h = hashlib.sha256(canonical_json(events).encode("ascii")).hexdigest()
    return f"{CANONICALIZATION_VERSION}:sha256:{h}"


def chain_digest(events: list[dict]) -> str:
    """Rolling hash chain over the canonical events: ``c1:chain-sha256:<hex>``.

    ``h_0 = sha256(b"")``; ``h_i = sha256(h_{i-1} || canonical_json(event_i))``.
    Where the flat digest names the stream as a whole, the chain commits to
    every *prefix*: truncating, inserting, reordering, or editing any event
    changes every subsequent link, so an append-only log can be spot-checked
    incrementally and a proof bundle's stream can be verified event by event.
    Same canonicalization rules as the digest — a version bump there is a
    version bump here.
    """
    h = hashlib.sha256(b"").digest()
    for e in events:
        line = json.dumps(
            canonical_event(e), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        h = hashlib.sha256(h + line.encode("ascii")).digest()
    return f"{CANONICALIZATION_VERSION}:chain-sha256:{h.hex()}"
