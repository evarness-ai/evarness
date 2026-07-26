"""evarness CLI — everything the library can do, headless.

Commands: validate | run | render | prove | verify | patterns. Every command
is recorded in the activity log (actor=cli) — full traceability.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from evarness.core.errors import EvarnessError
from evarness.core.executor import GraphValidationError, execute
from evarness.io.exporters import export_trace
from evarness.io.render import (
    RENDER_VERSION,
    RenderSubject,
    render,
    render_proof_browser,
)
from evarness.domains.agents.nodes import REGISTRY
from evarness.domains.agents.nodes.base import presentation
from evarness.domains.agents.patterns import (
    fixture_names,
    fixture_text,
    invariant_defs as pattern_invariant_defs,
    list_patterns,
    load_pattern,
)
from evarness.core.prove import prove, render_junit, render_proof_html, render_sarif, verify_proof
from evarness.core.graph import GraphModel, lint, migrate
from evarness.domains.agents.sim import load_fixture
from evarness.core.store import init_db, log_activity
from evarness.core.trace import canonical_trace, trace_digest


def _load_graph(path: str) -> GraphModel:
    doc = migrate(json.loads(Path(path).read_text()))
    return GraphModel.model_validate(doc)


def _presentation_for(graph: GraphModel) -> dict:
    """The domain's palette as plain data — io/render stays domain-agnostic;
    the CLI (a caller) is where domain presentation gets wired in."""
    return {t: presentation(t) for t in {n.type for n in graph.nodes}}


def cmd_validate(args) -> int:
    graph = _load_graph(args.graph)
    issues = lint(graph, REGISTRY)
    log_activity("cli.validate", graph.id, actor="cli", issues=len(issues))
    if not issues:
        print("OK — graph valid, no lint findings")
        return 0
    for i in issues:
        print(f"{i['level'].upper():<8} [{i['code']}] {i['message']}")
    return 1 if any(i["level"] == "error" for i in issues) else 0


def cmd_run(args) -> int:
    graph = _load_graph(args.graph)
    fixture = load_fixture(args.fixture)
    # --approve gate=decision (repeatable): supply human decisions so an
    # approval_gate resumes instead of pausing. Replay is deterministic.
    approvals: dict = {}
    for pair in args.approve or []:
        gate, _, decision = pair.partition("=")
        approvals[gate.strip()] = decision.strip() or "approve"
    # pattern-local contracts travel with the graph: an invariants.yaml next
    # to the graph file is loaded as highest-precedence definitions
    inv_file = Path(args.graph).parent / "invariants.yaml"
    invariant_defs = None
    if inv_file.exists():
        doc = yaml.safe_load(inv_file.read_text(encoding="utf-8")) or {}
        invariant_defs = doc.get("invariants") or {}
    log_activity("cli.run", graph.id, actor="cli", fixture=fixture.scenario)
    try:
        run = execute(
            graph,
            fixture,
            user_input=args.input,
            approvals=approvals,
            invariant_defs=invariant_defs,
        )
    except GraphValidationError as exc:
        print("Graph invalid:", exc)
        return 2
    digest = trace_digest(run.events)
    if args.json:
        print(
            json.dumps(
                {
                    "id": run.id,
                    "status": run.status,
                    "output": run.output,
                    "totals": run.totals,
                    "trace_digest": digest,
                    "invariants": run.invariants,
                    "events": run.events,
                },
                indent=2,
            )
        )
    else:
        for ev in run.events:
            print(
                f"{ev['seq']:>3}  {ev['type']:<22} {ev['node_id'] or '':<6} "
                f"{json.dumps(ev['payload'])[:110]}"
            )
        print(f"\nSTATUS: {run.status}" + (f" ({run.reason})" if run.reason else ""))
        print(f"TRACE: {len(run.events)} events, digest {digest}")
        if run.invariants:
            _print_verdicts(run.invariants)
        if run.status == "paused" and run.pending:
            g = run.pending["node_id"]
            print(f"PAUSED at {g}: {run.pending['prompt']}")
            print(f"  resume with:  --approve {g}=approve   (or {g}=reject)")
        print(f"OUTPUT: {run.output}")
    if args.trace_out:
        meta = {
            "run_id": run.id,
            "name": graph.name,
            "graph_id": graph.id,
            "status": run.status,
            "reason": run.reason,
            "seed": graph.params.seed,
            "provider": graph.params.provider,
            "fixture": fixture.scenario,
            "trace_digest": digest,
        }
        doc, _ = export_trace(args.trace_format, run.events, meta)
        Path(args.trace_out).write_text(doc)
        if not args.json:
            print(f"wrote {args.trace_out} ({args.trace_format})")
    if args.html:
        subject = RenderSubject(
            graph=graph,
            events=canonical_trace(run.events),
            verdicts=run.invariants,
            presentation=_presentation_for(graph),
            meta={
                "scenario": fixture.scenario,
                "status": run.status,
                "reason": run.reason,
                "provider": graph.params.provider,
                "seed": graph.params.seed,
                "deterministic": (
                    run.events[0]["payload"].get("deterministic") if run.events else None
                ),
            },
        )
        Path(args.html).write_text(render(subject))
        if not args.json:
            print(f"wrote {args.html} (render {RENDER_VERSION})")
    # CI gate: a completed run with a failed invariant is a failing exit code
    inv_failed = bool(run.invariants and run.invariants["failed"])
    return 0 if run.status == "completed" and not inv_failed else 1


def _bundle_graph(bundle: dict, graph_arg: str | None) -> tuple[GraphModel | None, str | None]:
    """Resolve a canvas graph for a proof bundle, honestly: an explicit
    ``--graph`` is hash-checked inside the renderer (mismatch raises); the
    bundle's pattern id is auto-resolved only if its hash still matches the
    pinned subject; otherwise the canvas is omitted with a printed note —
    never silently drawn from an unproven graph."""
    from evarness.core.prove import graph_hash

    subject = bundle.get("subject") or {}
    if graph_arg:
        return _load_graph(graph_arg), None  # renderer enforces the hash pin
    pattern = subject.get("pattern")
    if pattern:
        pattern_doc = load_pattern(pattern)
        if pattern_doc is None:
            return None, f"note: pattern '{pattern}' not found locally — canvas omitted"
        graph = GraphModel.model_validate(migrate(pattern_doc))
        if graph_hash(graph) != subject.get("graph_sha256"):
            return None, (
                f"note: pattern '{pattern}' on disk no longer matches the bundle's "
                "pinned graph — canvas omitted (the canvas is only ever drawn from "
                "the proven graph)"
            )
        return graph, None
    return None, (
        "note: the bundle pins its graph by hash only — pass --graph PATH to "
        "draw the canvas (it must match the pinned graph_sha256)"
    )


def cmd_render(args) -> int:
    """Draw a target as a self-contained HTML artifact — no execution.
    A graph file or pattern id gets a static canvas with lint findings
    (a run's replay comes from ``run --html``); a proof bundle gets the
    browsable proof: verdict badge, one viewer per scenario, the bundle's
    not_proven section, and the whole bundle embedded for offline verify."""
    target = args.target
    doc = json.loads(Path(target).read_text()) if target.endswith(".json") else None

    if doc is not None and "proof_version" in doc:
        graph, note = _bundle_graph(doc, args.graph)
        html = render_proof_browser(
            doc, graph=graph, presentation=_presentation_for(graph) if graph else None
        )
        subject = doc.get("subject") or {}
        log_activity("cli.render", str(subject.get("graph_id")), actor="cli", bundle=True)
        out = args.out or str(Path(target).with_suffix(".html"))
        Path(out).write_text(html)
        if note:
            print(note)
        print(f"wrote {out} (proof browser, render {RENDER_VERSION})")
        return 0

    if doc is None:
        doc = load_pattern(target)
        if doc is None:
            print(f"unknown target '{target}': not a .json path or a known pattern id")
            return 2
    graph = GraphModel.model_validate(migrate(doc))
    issues = lint(graph, REGISTRY)
    log_activity("cli.render", graph.id, actor="cli", issues=len(issues))
    subject_r = RenderSubject(
        graph=graph, presentation=_presentation_for(graph), meta={"lint": issues}
    )
    out = args.out or (
        str(Path(target).with_suffix(".html")) if target.endswith(".json") else f"{target}.html"
    )
    Path(out).write_text(render(subject_r, args.renderer))
    print(f"wrote {out} ({args.renderer}, render {RENDER_VERSION})")
    return 0


def cmd_prove(args) -> int:
    """Build a proof bundle: run every scenario twice, check invariants,
    emit proof.json (+ optional HTML). Exit 0 only when the proof holds."""
    approvals: dict = {}
    for pair in args.approve or []:
        gate, _, decision = pair.partition("=")
        approvals[gate.strip()] = decision.strip() or "approve"

    pattern_doc = load_pattern(args.target) if not args.target.endswith(".json") else None
    if pattern_doc:
        graph = GraphModel.model_validate(migrate(pattern_doc))
        names = args.fixture or fixture_names(args.target)
        scenarios = [(n, fixture_text(args.target, n) or "") for n in names]
        missing = [n for n, t in scenarios if not t]
        if missing:
            print(f"fixtures not found for pattern '{args.target}': {', '.join(missing)}")
            return 2
        inv_defs = pattern_invariant_defs(args.target) or None
        pattern_id = args.target
    else:
        graph = _load_graph(args.target)
        if not args.fixture:
            print("--fixture PATH is required when proving a graph file")
            return 2
        scenarios = [(Path(f).stem, Path(f).read_text()) for f in args.fixture]
        inv_file = Path(args.target).parent / "invariants.yaml"
        inv_defs = (
            (yaml.safe_load(inv_file.read_text()) or {}).get("invariants")
            if inv_file.exists()
            else None
        )
        pattern_id = None

    log_activity("cli.prove", graph.id, actor="cli", scenarios=[n for n, _ in scenarios])
    proof = prove(
        graph,
        scenarios,
        pattern_id=pattern_id,
        invariant_defs=inv_defs,
        approvals=approvals,
        include_events=not args.no_events,
    )
    if args.sign:
        from evarness.core.attest import AttestationError, sign_proof

        try:
            proof = sign_proof(proof, key_path=args.key)
        except AttestationError as exc:
            print(f"cannot sign: {exc}")
            return 2
        att = proof["attestation"]
        if att["key_created"]:
            print(
                f"new signing key created at {att['key_path']} "
                f"(share {att['key_path'].removesuffix('.pem')}.pub out of band)"
            )

    Path(args.out).write_text(json.dumps(proof, indent=2))
    if args.html:
        Path(args.html).write_text(render_proof_html(proof))
    if args.junit:
        Path(args.junit).write_text(render_junit(proof))
    if args.sarif:
        Path(args.sarif).write_text(render_sarif(proof))

    v = proof["verdict"]
    for sc in proof["scenarios"]:
        inv = sc["invariants"]
        inv_txt = f"{inv['passed']}\u2713/{inv['failed']}\u2717" if inv else "-"
        repro = {True: "reproduced", False: "NOT REPRODUCED", None: "-"}[sc["reproduced"]]
        print(
            f"  {sc['fixture']:<16} {sc['status']:<10} invariants {inv_txt:<8} "
            f"{repro:<15} {sc['trace_digest'][:24]}\u2026"
        )
    state = {True: "HOLDS", False: "FAILED", None: "PENDING"}[v["ok"]]
    print(
        f"PROOF: {state} \u2014 "
        f"{v['scenarios']} scenario(s), invariants_pass={v['invariants_pass']}, "
        f"reproduced={v['reproduced']}" + (f" ({v['note']})" if v.get("note") else "")
    )
    extras = [p for p in (args.html, args.junit, args.sarif) if p]
    print(f"wrote {args.out}" + (f" and {', '.join(extras)}" if extras else ""))
    return 0 if v["ok"] else 1


def cmd_verify(args) -> int:
    """Re-check a proof bundle without running anything (digests, chains,
    verdict consistency, signature). The reviewer-side half of `prove`."""
    doc = json.loads(Path(args.bundle).read_text())
    pubkey = args.pubkey
    if pubkey and Path(pubkey).is_file():  # accept a .pub file or raw b64
        pubkey = Path(pubkey).read_text().strip()
    result = verify_proof(doc, pubkey_b64=pubkey, require_signature=args.require_signature)
    log_activity("cli.verify", str(args.bundle), actor="cli", ok=result["ok"])
    for c in result["checks"]:
        mark = {True: "✓", False: "✗", None: "-"}[c["ok"]]
        where = f" [{c['scenario']}]" if c.get("scenario") else ""
        print(f"  {mark} {c['check']}{where} — {c['detail']}")
    print(
        f"VERIFY: {'OK' if result['ok'] else 'FAILED'} — bundle is "
        + ("internally consistent" if result["ok"] else "inconsistent or tampered; see ✗ above")
    )
    print(
        "note: verification proves consistency and (if signed) integrity — "
        "not that the runs happened; that trust reduces to the signer"
    )
    return 0 if result["ok"] else 1


def _print_verdicts(inv: dict) -> None:
    print(
        f"INVARIANTS: {inv['passed']} passed, {inv['failed']} FAILED"
        if inv["failed"]
        else f"INVARIANTS: {inv['passed']} passed"
    )
    for r in inv["results"]:
        mark = "✓" if r["ok"] else "✗"
        where = (
            f" (seq {', '.join(map(str, r['evidence_seq']))})"
            if not r["ok"] and r["evidence_seq"]
            else ""
        )
        print(f"  {mark} {r['id']}" + ("" if r["ok"] else f" — {r['detail']}{where}"))


def cmd_patterns(args) -> int:
    for p in list_patterns():
        print(
            f"{p['id']:<28} {p['name']:<34} [{p['source']:<7}] "
            f"fixtures: {', '.join(p['fixtures'])}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    init_db()
    ap = argparse.ArgumentParser(
        prog="evarness", description="Evarness engine — run harness graphs headless"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="lint a graph JSON file")
    v.add_argument("graph")
    v.set_defaults(fn=cmd_validate)

    r = sub.add_parser("run", help="execute a graph against a fixture")
    r.add_argument("graph")
    r.add_argument("--fixture", help="fixture YAML path (defaults to empty fixture)")
    r.add_argument("--input", help="override the user input")
    r.add_argument(
        "--approve",
        action="append",
        metavar="GATE=approve|reject",
        help="supply an approval_gate decision (repeatable); resumes a paused run",
    )
    r.add_argument("--json", action="store_true", help="machine-readable output")
    r.add_argument(
        "--trace-out",
        metavar="PATH",
        help="also export the run's trace to PATH (see --trace-format)",
    )
    r.add_argument(
        "--trace-format",
        default="jsonl",
        help="trace export format for --trace-out (jsonl, otlp, or a " "plugin-registered format)",
    )
    r.add_argument(
        "--html",
        metavar="PATH",
        help="also write a self-contained replay artifact "
        "(canvas + playhead over the canonical events + verdicts)",
    )
    r.set_defaults(fn=cmd_run)

    rd = sub.add_parser(
        "render", help="draw a graph, pattern, or proof bundle as a self-contained HTML artifact"
    )
    rd.add_argument("target", help="graph.json path, pattern id, or proof.json bundle")
    rd.add_argument("-o", "--out", help="output path (default: the target path with .html)")
    rd.add_argument("--renderer", default="html", help="registered renderer name (default: html)")
    rd.add_argument(
        "--graph",
        metavar="PATH",
        help="bundle browsing: draw the canvas from this graph "
        "(must match the bundle's pinned graph_sha256)",
    )
    rd.set_defaults(fn=cmd_render)

    pv = sub.add_parser("prove", help="build a proof bundle: scenarios + invariants + digests")
    pv.add_argument("target", help="pattern id, or path to a graph.json")
    pv.add_argument(
        "--fixture",
        action="append",
        help="fixture name (pattern) or YAML path (graph file); repeatable; "
        "default: all of the pattern's fixtures",
    )
    pv.add_argument(
        "--approve",
        action="append",
        metavar="GATE=approve|reject",
        help="approval decision applied to every scenario (repeatable)",
    )
    pv.add_argument("-o", "--out", default="proof.json", help="proof bundle path")
    pv.add_argument("--html", help="also write a self-contained HTML report")
    pv.add_argument(
        "--no-events", action="store_true", help="omit the canonical event streams (smaller bundle)"
    )
    pv.add_argument(
        "--junit", metavar="PATH", help="also write the verdicts as JUnit XML (CI test report)"
    )
    pv.add_argument(
        "--sarif",
        metavar="PATH",
        help="also write the violations as SARIF 2.1.0 (code-scanning report)",
    )
    pv.add_argument(
        "--sign",
        action="store_true",
        help="attach an Ed25519 attestation (key auto-created on first "
        "use; needs the [sign] extra)",
    )
    pv.add_argument(
        "--key",
        metavar="PATH",
        help="signing key path (default ~/.evarness/keys/proof_ed25519.pem)",
    )
    pv.set_defaults(fn=cmd_prove)

    vf = sub.add_parser(
        "verify", help="re-check a proof bundle: digests, chains, verdict, signature"
    )
    vf.add_argument("bundle", help="path to a proof.json")
    vf.add_argument("--pubkey", help="pin the signer: base64 key or a .pub file path")
    vf.add_argument(
        "--require-signature",
        action="store_true",
        help="fail if the bundle is unsigned or unverifiable",
    )
    vf.set_defaults(fn=cmd_verify)

    p = sub.add_parser("patterns", help="list built-in and user patterns")
    p.set_defaults(fn=cmd_patterns)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except EvarnessError as exc:
        # Refusals and misconfigurations are messages, not tracebacks (E9):
        # the family covers provider refusals, graph validation, registry
        # misses, and unknown export formats. Anything OUTSIDE the family is
        # a genuine bug and keeps its stack trace.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
