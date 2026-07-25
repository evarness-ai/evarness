"""Proof bundles — one reviewable artifact per harness: `evarness prove`.

A proof bundle answers, in one JSON document: *here is the exact subject (hashed),
here is the environment, here is the canonical evidence, here is what was checked,
here is what passed, here is what failed — and here is what this does NOT prove.*

For every scenario the prover:
1. runs the graph against the fixture,
2. runs it a SECOND time and compares trace digests — reproducibility is
   demonstrated, not asserted (deterministic runs only; real runs are honestly
   marked unreproducible),
3. records the invariant verdicts and the full canonical event stream.

The bundle's `verdict.ok` is tri-state (E8): **true** only when every declared
claim was actually checked and held — invariants evaluated and passing, every
deterministic scenario's digest reproduced; **null (pending)** when nothing
failed but a scenario paused at a human gate, so the claims were not checked
(a pending proof gates CI exactly like a failed one — it proves nothing yet);
**false** when a contract failed, a digest did not reproduce, or no invariants
were declared. A scenario's run *status* is otherwise recorded but never
judged — a failure-lab fixture that blocks is a governance success, and the
contracts (not the status) say what "correct" means.

The `not_proven` section is part of the format on purpose: a finite scenario set
verifies declared invariants under scripted conditions; it does not establish
universal safety, live-model determinism, or resistance to malicious tool
implementations. Proof bundles state their own limits.
"""

from __future__ import annotations

import hashlib
import json
import platform
import time
from importlib import metadata

import yaml

from evarness.core.executor import execute
from evarness.core.graph import GraphModel
from evarness.core.registry import SUBJECT_PINNERS, load_environment
from evarness.core.trace import (
    CANONICALIZATION_VERSION,
    canonical_trace,
    chain_digest,
    trace_digest,
)

PROOF_VERSION = "p2"

# honesty boilerplate — every bundle carries its own limits
NOT_PROVEN = [
    "universal safety: only the declared invariants were checked, only under "
    "the included scenarios",
    "live-model determinism: real-provider/real-tool runs are recorded as "
    "deterministic:false and their digests are not expected to reproduce",
    "tool implementation integrity: safety metadata is declared, not " "sandbox-enforced",
    "scenario coverage: behavior outside the included fixtures is untested",
    "producer honesty: digests, chains, and signatures prove the bundle is "
    "internally consistent and unaltered since signing — not that the runs "
    "happened as recorded; that trust reduces to the signer",
]

# packages whose versions are pinned into the environment block — the engine
# and its direct runtime dependencies (pyproject [project.dependencies])
_ENV_PACKAGES = ("evarness", "pydantic", "pyyaml")


def _engine_version() -> str:
    try:
        return metadata.version("evarness")
    except metadata.PackageNotFoundError:  # running from a bare checkout
        return "unknown"


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def graph_hash(graph: GraphModel) -> str:
    """Hash of the canonical graph document (sorted keys, compact, ascii) —
    names the exact subject under proof, position metadata included: the proof
    is over the artifact as shipped."""
    doc = json.dumps(
        graph.model_dump(by_alias=True), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return _sha256(doc.encode("ascii"))


def _canon_hash(obj) -> str:
    return _sha256(
        json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
        ).encode("ascii")
    )


def _invariant_defs_hash(declared: list[str], extra: dict | None) -> str | None:
    """Hash of the RESOLVED contract definitions for the declared ids — pins
    the contract *content*, so a bundle can't silently mean different
    assertions after a library edit. Unresolved ids hash as null (they fail
    their verdicts anyway — same honesty rule)."""
    if not declared:
        return None
    from evarness.core.invariants import load_invariant_defs

    defs = load_invariant_defs(extra)
    return _canon_hash({i: defs.get(i) for i in declared})


def _environment() -> dict:
    packages: dict[str, str | None] = {}
    for name in _ENV_PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    env = {
        "python": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "packages": packages,
    }
    env["dependencies_sha256"] = _canon_hash(packages)
    return env


def prove(
    graph: GraphModel,
    scenarios: list[tuple[str, str]],
    pattern_id: str | None = None,
    invariant_defs: dict | None = None,
    approvals: dict | None = None,
    include_events: bool = True,
) -> dict:
    """Build a proof bundle. `scenarios` is a list of (name, fixture_yaml_text);
    the raw text is what gets hashed, so a bundle names the exact fixture bytes."""
    engine_version = _engine_version()
    results = []
    all_invariants_pass = True
    all_reproduced = True
    invariants_evaluated = False
    reproduction_attempted = False
    paused_count = 0
    notes = list(NOT_PROVEN)

    for name, text in scenarios:
        fixture = load_environment(yaml.safe_load(text) or {})
        run = execute(
            graph, fixture, approvals=dict(approvals or {}), invariant_defs=invariant_defs
        )
        digest = trace_digest(run.events)
        started = next((e for e in run.events if e["type"] == "run_started"), None)
        deterministic = bool(started and started["payload"].get("deterministic"))

        reproduced = None
        if run.status != "paused":
            if deterministic:
                second = execute(
                    graph, fixture, approvals=dict(approvals or {}), invariant_defs=invariant_defs
                )
                reproduced = trace_digest(second.events) == digest
                reproduction_attempted = True
                all_reproduced = all_reproduced and reproduced
            else:
                notes.append(
                    f"scenario '{name}': not deterministic — digest "
                    "identifies the trace but is not reproducible"
                )

        inv = run.invariants
        if run.status == "paused":
            paused_count += 1
            notes.append(
                f"scenario '{name}': paused awaiting a human decision — "
                "invariants not evaluated; prove the resumed branches by "
                "passing --approve"
            )
        elif inv:
            invariants_evaluated = True
            all_invariants_pass = all_invariants_pass and inv["failed"] == 0

        scenario: dict = {
            "fixture": name,
            "fixture_sha256": _sha256(text.encode("utf-8")),
            "status": run.status,
            "reason": run.reason,
            "deterministic": deterministic,
            "trace_digest": digest,
            "event_chain": chain_digest(run.events),
            "reproduced": reproduced,
            "events_count": len(run.events),
            "invariants": inv,
        }
        if include_events:
            scenario["events"] = canonical_trace(run.events)
        results.append(scenario)

    declared = list(graph.params.invariants)
    pinned: dict = {}
    for pinner in SUBJECT_PINNERS:
        pinned.update(pinner(graph) or {})
    tools = pinned.get("tool_manifests") or {}
    if any(h is None for h in tools.values()):
        missing = sorted(t for t, h in tools.items() if h is None)
        notes.append(
            "tool manifests unavailable for: "
            + ", ".join(missing)
            + " — those tool contracts are not pinned by this bundle"
        )
    return {
        "proof_version": PROOF_VERSION,
        "generated_at": round(time.time(), 2),
        "engine": {"evarness": engine_version, "canonicalization": CANONICALIZATION_VERSION},
        "environment": _environment(),
        "subject": {
            "graph_id": graph.id,
            "graph_name": graph.name,
            "graph_sha256": graph_hash(graph),
            "pattern": pattern_id,
            "seed": graph.params.seed,
            "provider": graph.params.provider,
            "invariants_declared": declared,
            "invariant_defs_sha256": _invariant_defs_hash(declared, invariant_defs),
            "tools": tools,
        },
        "scenarios": results,
        "verdict": _verdict(
            len(results),
            declared=bool(declared),
            invariants_pass=all_invariants_pass if invariants_evaluated else None,
            reproduced=all_reproduced if reproduction_attempted else None,
            paused=paused_count,
        ),
        "not_proven": notes,
    }


def _verdict(
    scenario_count: int,
    declared: bool,
    invariants_pass: bool | None,
    reproduced: bool | None,
    paused: int,
) -> dict:
    """The tri-state verdict (E8). `invariants_pass`/`reproduced` are None when
    nothing was evaluated/attempted — vacuous truth is never reported as truth.
    `ok` is: false when the proof failed (nothing declared, a contract failed,
    or a digest did not reproduce); **null (pending)** when nothing failed but
    a scenario paused, so the bundle proves nothing yet; true only when every
    declared claim was actually checked and held. Shared verbatim by
    `verify_proof` so the producer and reviewer can never disagree."""
    failed = (not declared) or invariants_pass is False or reproduced is False
    ok = False if failed else (None if paused else True)
    if not declared:
        note = (
            "graph declares no invariants — nothing was asserted, so "
            "nothing was proven (params.invariants is empty)"
        )
    elif ok is None:
        note = (
            f"{paused} scenario(s) paused awaiting a human decision — nothing "
            "failed, but the paused scenarios' invariants were not evaluated "
            "and reproduction was not attempted; the proof is pending until "
            "the resumed branches run (pass --approve)"
        )
    else:
        note = None
    return {
        "scenarios": scenario_count,
        "invariants_pass": invariants_pass,
        "reproduced": reproduced,
        "ok": ok,
        "note": note,
    }


# ------------------------------------------------------------ verification


def verify_proof(
    proof: dict, pubkey_b64: str | None = None, require_signature: bool = False
) -> dict:
    """Re-check a proof bundle without running anything: recompute every
    scenario's digest and hash chain from the included canonical events,
    confirm the verdict is consistent with the scenario rows, and verify the
    signature when one is present.

    Honesty note (also in the bundle's not_proven): these checks prove the
    bundle is INTERNALLY CONSISTENT and — if signed — unaltered since signing.
    They cannot prove the recorded runs actually happened; that trust reduces
    to the signer. Checks are {check, [scenario], ok, detail} with ok=None
    meaning skipped-with-reason, never silently absent."""
    checks: list[dict] = []

    version = proof.get("proof_version")
    checks.append(
        {
            "check": "proof version recognized",
            "ok": version in ("p1", "p2"),
            "detail": f"proof_version={version}",
        }
    )

    for sc in proof.get("scenarios", []):
        name = sc.get("fixture", "?")
        events = sc.get("events")
        if events is None:
            checks.append(
                {
                    "check": "digest recomputes",
                    "scenario": name,
                    "ok": None,
                    "detail": "bundle built with --no-events — the "
                    "stream is not included, digest not "
                    "independently checkable",
                }
            )
            continue
        checks.append(
            {
                "check": "digest recomputes",
                "scenario": name,
                "ok": trace_digest(events) == sc.get("trace_digest"),
                "detail": sc.get("trace_digest", ""),
            }
        )
        if sc.get("event_chain") is not None:  # p1 bundles have no chain
            checks.append(
                {
                    "check": "event chain recomputes",
                    "scenario": name,
                    "ok": chain_digest(events) == sc.get("event_chain"),
                    "detail": sc.get("event_chain", ""),
                }
            )
        if sc.get("events_count") is not None:
            checks.append(
                {
                    "check": "event count matches",
                    "scenario": name,
                    "ok": len(events) == sc.get("events_count"),
                    "detail": f"{len(events)} events",
                }
            )

    v = proof.get("verdict") or {}
    scenarios = proof.get("scenarios", [])
    evaluated = [sc["invariants"] for sc in scenarios if sc.get("invariants") is not None]
    attempted = [sc["reproduced"] for sc in scenarios if sc.get("reproduced") is not None]
    expected = _verdict(
        len(scenarios),
        declared=bool((proof.get("subject") or {}).get("invariants_declared")),
        invariants_pass=all(i.get("failed", 0) == 0 for i in evaluated) if evaluated else None,
        reproduced=all(attempted) if attempted else None,
        paused=sum(1 for sc in scenarios if sc.get("status") == "paused"),
    )
    checks.append(
        {
            "check": "verdict consistent with scenario rows",
            "ok": all(v.get(k) == expected[k] for k in ("invariants_pass", "reproduced", "ok")),
            "detail": f"ok={v.get('ok')} invariants_pass="
            f"{v.get('invariants_pass')} reproduced={v.get('reproduced')}",
        }
    )

    if proof.get("attestation"):
        try:
            from evarness.core.attest import verify_attestation

            sig = verify_attestation(proof, pubkey_b64=pubkey_b64)
            checks.append({"check": "signature", "ok": sig["ok"], "detail": sig["detail"]})
        except ValueError as exc:  # crypto not installed
            checks.append(
                {
                    "check": "signature",
                    "ok": False if require_signature else None,
                    "detail": str(exc),
                }
            )
    else:
        checks.append(
            {
                "check": "signature",
                "ok": False if require_signature else None,
                "detail": "bundle is unsigned"
                + (" (required)" if require_signature else " — integrity checks above still hold"),
            }
        )

    return {"ok": all(c["ok"] is not False for c in checks), "checks": checks}


# ----------------------------------------------------- CI report projections
# JUnit and SARIF render VERDICTS (proof bundles), not traces — trace formats
# live in exporters.py. Both carry the trace digest so a CI failure can always
# be traced back to the exact canonical evidence.


def _xml_esc(x) -> str:
    return (
        str(x)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_junit(proof: dict) -> str:
    """Proof bundle -> JUnit XML: one testsuite per scenario; one testcase per
    invariant verdict plus one for digest reproduction. A graph that declares
    no invariants renders a FAILING case — NOTHING ASSERTED must break CI the
    same way the CLI exit code does, never pass silently."""
    s = proof["subject"]
    suite_cls = s["graph_name"] or s["graph_id"] or "graph"
    suites = []
    total = failures = skipped = 0

    for sc in proof["scenarios"]:
        cases = []
        cls = _xml_esc(f"{suite_cls}.{sc['fixture']}")
        for r in (sc["invariants"] or {}).get("results", []):
            total += 1
            body = ""
            if not r["ok"]:
                failures += 1
                seqs = ", ".join(map(str, r["evidence_seq"])) or "-"
                body = (
                    f'<failure message="{_xml_esc(r["detail"] or "invariant failed")}">'
                    f"evidence seq: {_xml_esc(seqs)}\n"
                    f'trace: {_xml_esc(sc["trace_digest"])}</failure>'
                )
            cases.append(
                f'<testcase classname="{cls}" '
                f'name="invariant: {_xml_esc(r["id"])}">{body}</testcase>'
            )
        total += 1
        if sc["reproduced"] is True:
            body = ""
        elif sc["reproduced"] is False:
            failures += 1
            body = (
                f'<failure message="digest did not reproduce">'
                f'trace: {_xml_esc(sc["trace_digest"])}</failure>'
            )
        else:
            skipped += 1
            why = (
                "run paused awaiting a human decision"
                if sc["status"] == "paused"
                else "not deterministic — reproduction not expected"
            )
            body = f'<skipped message="{_xml_esc(why)}"/>'
        cases.append(f'<testcase classname="{cls}" name="digest reproduced">{body}</testcase>')
        suites.append((sc["fixture"], cases))

    if not s["invariants_declared"]:
        total += 1
        failures += 1
        note = proof["verdict"]["note"] or "graph declares no invariants"
        suites.append(
            (
                "(contracts)",
                [
                    f'<testcase classname="{_xml_esc(suite_cls)}" name="invariants declared">'
                    f'<failure message="{_xml_esc(note)}"/></testcase>'
                ],
            )
        )
    elif proof["verdict"]["ok"] is None:
        # PENDING must break CI the same way NOTHING ASSERTED does — a proof
        # that proved nothing yet must never pass a merge gate silently (E8)
        total += 1
        failures += 1
        note = proof["verdict"]["note"] or "proof pending — a scenario paused"
        suites.append(
            (
                "(pending)",
                [
                    f'<testcase classname="{_xml_esc(suite_cls)}" name="proof complete">'
                    f'<failure message="{_xml_esc(note)}"/></testcase>'
                ],
            )
        )

    suite_xml = "".join(
        f'<testsuite name="{_xml_esc(f"{suite_cls}:{name}")}" '
        f'tests="{len(cases)}">{"".join(cases)}</testsuite>'
        for name, cases in suites
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuites name="evarness prove" tests="{total}" '
        f'failures="{failures}" skipped="{skipped}">{suite_xml}</testsuites>\n'
    )


def render_sarif(proof: dict) -> str:
    """Proof bundle -> SARIF 2.1.0: rules are the declared invariant contracts
    (plus the reproducibility check), results are the violations. NOTHING
    ASSERTED surfaces as a warning-level result, not an empty (passing) log."""
    s, v = proof["subject"], proof["verdict"]
    rules = [
        {
            "id": rid,
            "shortDescription": {
                "text": f"invariant contract '{rid}' must hold over the event stream"
            },
        }
        for rid in s["invariants_declared"]
    ]
    rules.append(
        {
            "id": "digest-reproducibility",
            "shortDescription": {
                "text": "a deterministic scenario must reproduce its canonical trace digest"
            },
        }
    )
    rules.append(
        {
            "id": "nothing-asserted",
            "shortDescription": {"text": "a proof is only as strong as its declared invariants"},
        }
    )
    rules.append(
        {
            "id": "proof-pending",
            "shortDescription": {
                "text": "a paused scenario leaves the proof pending — nothing "
                "is proven until the resumed branches run"
            },
        }
    )

    results = []
    for sc in proof["scenarios"]:
        loc = [{"logicalLocations": [{"name": sc["fixture"], "kind": "scenario"}]}]
        for r in (sc["invariants"] or {}).get("results", []):
            if r["ok"]:
                continue
            results.append(
                {
                    "ruleId": r["id"],
                    "level": "error",
                    "message": {
                        "text": f"scenario '{sc['fixture']}': "
                        f"{r['detail'] or 'invariant failed'}"
                    },
                    "locations": loc,
                    "properties": {
                        "evidence_seq": r["evidence_seq"],
                        "trace_digest": sc["trace_digest"],
                        "fixture_sha256": sc["fixture_sha256"],
                    },
                }
            )
        if sc["reproduced"] is False:
            results.append(
                {
                    "ruleId": "digest-reproducibility",
                    "level": "error",
                    "message": {
                        "text": f"scenario '{sc['fixture']}': trace digest did "
                        "not reproduce on the second run"
                    },
                    "locations": loc,
                    "properties": {"trace_digest": sc["trace_digest"]},
                }
            )
    if not s["invariants_declared"]:
        results.append(
            {
                "ruleId": "nothing-asserted",
                "level": "warning",
                "message": {
                    "text": v["note"] or "graph declares no invariants — nothing was asserted"
                },
            }
        )
    elif v["ok"] is None:
        results.append(
            {
                "ruleId": "proof-pending",
                "level": "warning",
                "message": {"text": v["note"] or "proof pending — a scenario paused"},
            }
        )

    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "evarness",
                        "version": proof["engine"]["evarness"],
                        "informationUri": "https://github.com/sathishksomasundaram/evarness",
                        "rules": rules,
                    }
                },
                "results": results,
                "properties": {
                    "proof_version": proof["proof_version"],
                    "graph_sha256": s["graph_sha256"],
                    "verdict": v,
                },
            }
        ],
    }
    return json.dumps(doc, indent=2)


# ------------------------------------------------------------------ HTML report


def _html_env(proof: dict, esc) -> str:
    """Environment + attestation lines for the report header (p2 bundles)."""
    parts = []
    env = proof.get("environment")
    if env:
        pkgs = " · ".join(f"{k} {v or '?'}" for k, v in env["packages"].items())
        parts.append(
            f"<br>env python {esc(env['python'])} · " f"{esc(env['platform'])} · {esc(pkgs)}"
        )
    att = proof.get("attestation")
    if att:
        parts.append(f"<br>signed {esc(att['algorithm'])} · " f"key {esc(att['public_key'][:16])}…")
    return "".join(parts)


def render_proof_html(proof: dict) -> str:
    """Self-contained single-file report — inline CSS, no external assets."""
    v, s = proof["verdict"], proof["subject"]
    ok = v["ok"]
    if not s["invariants_declared"]:
        badge, color = "NOTHING ASSERTED", "#b35900"
    elif ok is True:
        badge, color = "PROOF HOLDS", "#1a7f37"
    elif ok is None:
        badge, color = "PROOF PENDING", "#b35900"
    else:
        badge, color = "PROOF FAILED", "#c62828"

    def esc(x):
        return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rows = []
    for sc in proof["scenarios"]:
        inv_rows = ""
        if sc["invariants"]:
            inv_rows = "".join(
                f"<tr><td>{'✓' if r['ok'] else '✗'}</td><td>{esc(r['id'])}</td>"
                f"<td>{esc(r['detail'] or '')}"
                + (
                    f" (seq {', '.join(map(str, r['evidence_seq']))})"
                    if not r["ok"] and r["evidence_seq"]
                    else ""
                )
                + "</td></tr>"
                for r in sc["invariants"]["results"]
            )
            inv_rows = (
                f"<table class='inv'><tr><th></th><th>invariant</th>"
                f"<th>detail</th></tr>{inv_rows}</table>"
            )
        repro = {True: "✓ digest reproduced", False: "✗ DIGEST DID NOT REPRODUCE", None: "—"}[
            sc["reproduced"]
        ]
        chain = sc.get("event_chain")
        rows.append(f"""
      <div class="card">
        <h3>{esc(sc['fixture'])} <span class="st st-{esc(sc['status'])}">{esc(sc['status'])}</span></h3>
        <p class="mono">trace {esc(sc['trace_digest'])}{('<br>chain ' + esc(chain)) if chain else ''}</p>
        <p class="mono">fixture {esc(sc['fixture_sha256'])} · {sc['events_count']} events ·
           deterministic: {str(sc['deterministic']).lower()} · {esc(repro)}</p>
        {inv_rows}
      </div>""")

    notes = "".join(f"<li>{esc(n)}</li>" for n in proof["not_proven"])
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Proof — {esc(s['graph_name'] or s['graph_id'])}</title>
<style>
 body{{font:14px/1.5 -apple-system,Segoe UI,sans-serif;margin:2rem auto;max-width:900px;
      padding:0 1rem;color:#222}}
 .badge{{display:inline-block;padding:.3rem .8rem;border-radius:6px;color:#fff;
        background:{color};font-weight:700}}
 .mono{{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#555;
       overflow-wrap:anywhere}}
 .card{{border:1px solid #ddd;border-radius:8px;padding: .2rem 1rem 1rem;margin:1rem 0}}
 .st{{font-size:11px;padding:.1rem .5rem;border-radius:9px;vertical-align:middle}}
 .st-completed{{background:#e6f4ea;color:#1a7f37}} .st-blocked{{background:#fdecea;color:#c62828}}
 .st-paused{{background:#fff4e5;color:#b35900}} .st-failed{{background:#fdecea;color:#c62828}}
 table.inv{{border-collapse:collapse;width:100%;font-size:13px}}
 table.inv td,table.inv th{{border-top:1px solid #eee;padding:.3rem .5rem;text-align:left}}
 .np{{background:#fafafa;border:1px dashed #ccc;border-radius:8px;padding:.5rem 1.5rem}}
</style></head><body>
<h1>Proof bundle <span class="badge">{esc(badge)}</span></h1>
<p><b>{esc(s['graph_name'] or s['graph_id'])}</b>
   {('· pattern <code>' + esc(s['pattern']) + '</code>') if s['pattern'] else ''}</p>
<p class="mono">graph {esc(s['graph_sha256'])}<br>
   engine evarness {esc(proof['engine']['evarness'])} ·
   canonicalization {esc(proof['engine']['canonicalization'])} ·
   seed {esc(s['seed'])} · provider {esc(s['provider'])}{_html_env(proof, esc)}</p>
<p>Invariants declared: {', '.join('<code>' + esc(i) + '</code>' for i in s['invariants_declared']) or '<i>none</i>'}</p>
{''.join(rows)}
<h2>What this does not prove</h2>
<div class="np"><ul>{notes}</ul></div>
</body></html>"""
