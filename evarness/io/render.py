"""Render artifacts — a graph, a run, and its verdicts as one self-contained
HTML file; a proof bundle as a browsable page built from the same pieces.

Where exporters project *traces* into interchange formats and the proof
renderers report *verdicts*, a renderer draws a *subject*: the graph's shape,
optionally the run that traversed it (a playhead over the canonical event
stream), and optionally the judgment about that run (invariant verdicts).
The proof browser (:func:`render_proof_browser`) composes one such viewer per
scenario under the bundle's tri-state verdict badge.

The artifact carries the product's rules in its own layout:

* **The digest travels inside** (the exporters' rule): the provenance bar
  names the trace, and the embedded data island contains the canonical events
  themselves — a reader can recompute the digest from the artifact alone. The
  proof browser embeds the WHOLE bundle: extract the island's ``bundle`` key
  to a file and ``evarness verify`` re-checks it, signature included.
* **Evidence and judgment never contaminate each other** (E4): events render
  in an evidence pane, verdicts in a separate judgment pane with their own
  data key; a verdict's ``evidence_seq`` link scrubs the playhead to the cited
  event — judgment points at evidence, it never rewrites it.
* **A mandatory not-established footer** (prove's ``not_proven`` rule): every
  artifact states what it is not — derived evidence, no claim the run
  happened on any particular machine, honest lines for paused runs
  (invariants never evaluated) and zero-contract runs (nothing asserted).
  The proof browser renders the bundle's own ``not_proven`` section verbatim.
* **The canvas never lies about identity**: the proof browser draws a graph
  only when its ``graph_hash`` matches the bundle's pinned ``graph_sha256`` —
  a mismatched graph raises loudly, an unavailable graph is an honest
  omission, never a silent substitute. A signed bundle gets a line saying the
  signature is NOT checked by this page (that is ``evarness verify``'s job —
  a page must not vouch for its own integrity).

Self-containment is a hard guarantee, not a style choice: no external
requests of any kind (the test suite greps the output for external origins),
data enters the page through a JSON island read with ``JSON.parse``, dynamic
DOM goes through ``textContent`` only, and a CSP meta tag pins
``default-src 'none'``. Hostile fixture content must render inert in an
auditor's browser.

Determinism: the artifact embeds the CANONICAL events (no wall-clock), layout
derives from the same topological order as the determinism contract, and all
serialization is sorted/compact/ascii — so for a deterministic run the whole
file is byte-stable and pinned by a golden test. ``RENDER_VERSION`` ("r2" —
r1 restructured into per-viewer scoping so one page can hold a viewer per
scenario) stamps the artifact; ANY change to rendered bytes bumps it, and the
golden pin moves only with the version. This is a derived-evidence version,
deliberately not part of the ``c1`` digest contract. (Proof-browser pages
inherit the bundle's ``generated_at`` wall clock, so they are byte-stable per
bundle, not per subject.)

Extension point: register your own renderer by name —

    from evarness.io.render import register_renderer

    @register_renderer("svg")
    def render_svg(subject):
        ...return the document as a str...

The module is domain-agnostic (io consumes public core surfaces only): node
icons/labels arrive as plain data in ``RenderSubject.presentation``, wired in
by the caller — a third-party domain renders correctly the day it passes its
own presentation table, with ``⬡`` as the honest fallback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html import escape
from string import Template
from typing import Callable

from evarness.core.errors import EvarnessError
from evarness.core.graph import GraphModel, topological_order
from evarness.core.prove import graph_hash
from evarness.core.trace import canonical_json, trace_digest

RENDER_VERSION = "r2"

_FALLBACK_PRESENTATION = {"icon": "⬡", "label": None}

# node states a playhead can put a node into; terminal event types that mark them
_FAIL_EVENTS = ("policy_violation", "engine_error")


class RenderFormatError(EvarnessError, ValueError):
    """Unknown renderer name — loud, naming the renderers that do exist."""


class RenderMismatchError(EvarnessError):
    """A graph offered for the canvas does not match the bundle's pinned
    subject — drawing it next to proven evidence would lie."""


@dataclass
class RenderSubject:
    """Everything a renderer may draw. ``graph`` is the only required part:
    no ``events`` means a static canvas; no ``verdicts`` means the judgment
    pane states that honestly instead of staying silent."""

    graph: GraphModel
    events: list[dict] | None = None  # canonical form, seq-ordered
    verdicts: dict | None = None  # RunResult.invariants — judgment, kept apart
    presentation: dict[str, dict] = field(default_factory=dict)  # {type: {icon,label}}
    meta: dict = field(default_factory=dict)  # scenario/seed/status/lint/…


Renderer = Callable[[RenderSubject], str]

_RENDERERS: dict[str, Renderer] = {}


def register_renderer(name: str):
    """Register a renderer under ``name`` (a callable RenderSubject -> str)."""

    def deco(fn: Renderer) -> Renderer:
        _RENDERERS[name.lower()] = fn
        return fn

    return deco


def available_renderers() -> list[str]:
    return sorted(_RENDERERS)


def render(subject: RenderSubject, renderer: str = "html") -> str:
    fn = _RENDERERS.get((renderer or "").lower())
    if fn is None:
        raise RenderFormatError(
            f"unknown renderer '{renderer}' — available: {', '.join(available_renderers())}"
        )
    return fn(subject)


# ---------------------------------------------------------------- layout

NODE_W, NODE_H = 190, 64
GAP_X, GAP_Y, MARGIN = 260, 104, 40


def layered_layout(graph: GraphModel) -> dict[str, tuple[int, int]]:
    """Deterministic layered positions: layer = longest path from the roots,
    computed over the same topological order (id tie-break) as the determinism
    contract; row = sorted index within the layer. Authored ``position``
    fields win per node — the algorithm is the fallback, not the boss."""
    layer: dict[str, int] = {}
    for nid in topological_order(graph):
        preds = [e.from_ for e in graph.edges if e.to == nid and e.from_ in layer]
        layer[nid] = max((layer[p] + 1 for p in preds), default=0)
    rows: dict[int, list[str]] = {}
    for nid in sorted(layer):
        rows.setdefault(layer[nid], []).append(nid)
    pos: dict[str, tuple[int, int]] = {}
    for lx, ids in sorted(rows.items()):
        for iy, nid in enumerate(ids):
            pos[nid] = (MARGIN + lx * GAP_X, MARGIN + iy * GAP_Y)
    for n in graph.nodes:
        if n.position is not None:
            pos[n.id] = (int(n.position.x), int(n.position.y))
    return pos


# ---------------------------------------------------------------- data island


def _json_compact(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _island(parts: list[str]) -> str:
    """Assemble the JSON island from pre-serialized ``"key":value`` parts.
    Every ``<`` is emitted as ``\\u003c``: no tag can ever open inside the
    island (this also defeats the script-data double-escape trick, where
    ``<!--<script>`` would keep a plain ``</``-escape from closing the
    element). The escape is JSON-transparent — ``JSON.parse`` returns the
    original bytes, so digest recomputation is unaffected."""
    return ("{" + ",".join(parts) + "}").replace("<", "\\u003c")


def _subject_island(subject: RenderSubject, digest: str | None) -> str:
    """``canonical_events`` is spliced in via ``canonical_json`` — the exact
    digest input, so a reader can recompute ``meta.trace_digest`` from this
    island alone."""
    meta = dict(subject.meta)
    meta["render_version"] = RENDER_VERSION
    if digest:
        meta["trace_digest"] = digest
    parts = ['"meta":' + _json_compact(meta)]
    if subject.events is not None:
        parts.append('"canonical_events":' + canonical_json(subject.events))
    if subject.verdicts is not None:
        parts.append('"verdicts":' + _json_compact(subject.verdicts))
    return _island(parts)


# ---------------------------------------------------------------- html pieces


def _canvas_svg(graph: GraphModel, presentation: dict, node_index: dict[str, int]) -> str:
    pos = layered_layout(graph)
    width = max((x for x, _ in pos.values()), default=0) + NODE_W + MARGIN
    height = max((y for _, y in pos.values()), default=0) + NODE_H + MARGIN
    parts: list[str] = []
    for e in graph.edges:
        if e.from_ not in pos or e.to not in pos:
            continue
        x1, y1 = pos[e.from_][0] + NODE_W, pos[e.from_][1] + NODE_H // 2
        x2, y2 = pos[e.to][0], pos[e.to][1] + NODE_H // 2
        parts.append(
            f'<path class="edge" d="M {x1} {y1} C {x1 + 60} {y1}, {x2 - 60} {y2}, ' f'{x2} {y2}"/>'
        )
        if e.from_port != "out" or e.to_port != "in":
            mx, my = (x1 + x2) // 2, (y1 + y2) // 2 - 6
            parts.append(
                f'<text class="port" x="{mx}" y="{my}">'
                f"{escape(e.from_port)}→{escape(e.to_port)}</text>"
            )
    for n in graph.nodes:
        if n.id not in pos:
            continue
        x, y = pos[n.id]
        p = presentation.get(n.type) or _FALLBACK_PRESENTATION
        label = n.label or p.get("label") or n.type
        parts.append(
            f'<g class="node idle" data-gnode="{node_index[n.id]}" '
            f'transform="translate({x},{y})">'
            f'<rect width="{NODE_W}" height="{NODE_H}" rx="10"/>'
            f'<text class="icon" x="14" y="40">{escape(p.get("icon") or "⬡")}</text>'
            f'<text class="label" x="48" y="28">{escape(str(label))}</text>'
            f'<text class="sub" x="48" y="48">{escape(n.id)} · {escape(n.type)}</text>'
            f"</g>"
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="graph canvas">' + "".join(parts) + "</svg>"
    )


def _lint_strip(meta: dict) -> str:
    issues = meta.get("lint") or []
    if not issues:
        return ""
    rows = "".join(
        f'<li class="{escape(i["level"])}">{escape(i["level"].upper())} '
        f'[{escape(i["code"])}] {escape(i["message"])}</li>'
        for i in issues
    )
    return f'<ul class="lint">{rows}</ul>'


def _event_rows(events: list[dict], node_index: dict[str, int]) -> str:
    rows = []
    for ev in events:
        nid = ev.get("node_id")
        nidx = node_index.get(nid, "") if nid else ""
        cls = "ev bad" if ev["type"] in _FAIL_EVENTS or ev["type"] == "run_failed" else "ev"
        payload = ev.get("payload") or {}
        payload_html = ""
        if payload:
            payload_html = (
                f"<details><summary>payload</summary>"
                f"<pre>{escape(_json_compact(payload))}</pre></details>"
            )
        rows.append(
            f'<div class="{cls}" data-ev data-seq="{ev["seq"]}" data-nidx="{nidx}" '
            f'data-type="{escape(ev["type"])}">'
            f'<span class="seq">{ev["seq"]}</span> '
            f'<span class="type">{escape(ev["type"])}</span> '
            f'<span class="nid">{escape(nid or "")}</span>{payload_html}</div>'
        )
    return "".join(rows)


def _verdict_rows(verdicts: dict) -> str:
    rows = []
    for r in verdicts["results"]:
        mark = "✓" if r["ok"] else "✗"
        seeks = "".join(
            f'<button type="button" class="seek" data-seek="{int(s)}">seq {int(s)}</button>'
            for s in (r["evidence_seq"] if not r["ok"] else [])
        )
        detail = "" if r["ok"] else f' — {escape(r["detail"] or "")}'
        rows.append(
            f'<div class="verdict {"ok" if r["ok"] else "fail"}">'
            f'<span class="mark">{mark}</span> {escape(r["id"])}{detail} {seeks}</div>'
        )
    summary = (
        f'{verdicts["passed"]} passed, {verdicts["failed"]} failed'
        if verdicts["failed"]
        else f'{verdicts["passed"]} passed'
    )
    return f'<p class="summary">{summary}</p>' + "".join(rows)


def _judgment_block(verdicts: dict | None, declared: list[str], status: str | None) -> str:
    if verdicts is not None:
        return _verdict_rows(verdicts)
    if status is None:
        return '<p class="quiet">No run attached — nothing to judge.</p>'
    if declared:
        return (
            '<p class="pending">Declared but NOT evaluated '
            f"({', '.join(escape(d) for d in declared)}) — the run paused before "
            "judgment; nothing here claims these hold.</p>"
        )
    return (
        '<p class="pending">No invariants declared — nothing was asserted, '
        "and nothing should be read as passing.</p>"
    )


def _controls(n_events: int) -> str:
    def btn(act: str, label: str, glyph: str) -> str:
        return (
            f'<button type="button" data-act="{act}" aria-label="{label}" '
            f'title="{label}">{glyph}</button>'
        )

    return (
        btn("first", "First event", "⏮")
        + btn("prev", "Previous event", "◀")
        + btn("play", "Play / Pause", "▶")
        + btn("next", "Next event", "▶▎")
        + btn("last", "Last event", "⏭")
        + f'<input type="range" data-ph aria-label="Playhead" min="-1" '
        f'max="{n_events - 1}" value="{n_events - 1}" step="1">'
        "<span data-phlabel></span>"
    )


def _viewer(canvas: str, controls: str, evidence: str, judgment: str, head: str = "") -> str:
    return (
        f'<section class="viewer" data-viewer>{head}'
        f'<div class="panel canvas">{canvas}'
        f'<div class="controls">{controls}</div></div>'
        f'<div class="side">'
        f'<div class="panel"><h2>Evidence — canonical events</h2>{evidence}</div>'
        f'<div class="panel"><h2>Judgment — invariant verdicts</h2>{judgment}</div>'
        f"</div></section>"
    )


def _footer(lines: list[str], title: str = "What this artifact does not establish") -> str:
    items = "".join(f"<li>{escape(ln)}</li>" for ln in lines)
    return f"<h2>{escape(title)}</h2><ul>{items}</ul>"


def _provenance(subject: RenderSubject, digest: str | None) -> str:
    g, meta = subject.graph, subject.meta
    bits = [f"<strong>{escape(g.name or g.id)}</strong>"]
    for key in ("scenario", "status", "provider"):
        if meta.get(key):
            bits.append(f"{key} {escape(str(meta[key]))}")
    bits.append(f"seed {g.params.seed}")
    if digest:
        bits.append(f"digest <code>{escape(digest)}</code>")
    if meta.get("deterministic") is not None:
        bits.append(f"deterministic {str(bool(meta['deterministic'])).lower()}")
    bits.append(f"renderer {RENDER_VERSION}")
    return " · ".join(bits)


_STYLE = """
:root { color-scheme: light dark; --bg:#f6f7f9; --fg:#1c2733; --card:#ffffff;
  --line:#d5dbe3; --quiet:#6b7684; --ok:#1a7f37; --bad:#c62828; --warn:#b35900;
  --active:#1f6feb; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1420; --fg:#dbe4ee;
  --card:#16202e; --line:#2b3a4d; --quiet:#8b98a8; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:14px/1.45 system-ui, sans-serif; }
header { padding:10px 16px; border-bottom:1px solid var(--line);
  background:var(--card); }
main { padding:12px 16px; }
.viewer { display:grid; grid-template-columns: minmax(0,1fr) 380px; gap:12px;
  margin-bottom:16px; }
.viewer .head { grid-column: 1 / -1; padding:6px 10px; border:1px solid
  var(--line); border-radius:10px; background:var(--card); }
.panel { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:10px; overflow:auto; }
.canvas { overflow:auto; }
.side { display:flex; flex-direction:column; gap:12px; min-width:0; }
.side .panel { max-height:44vh; }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--quiet); margin:4px 0 8px; }
.badge { display:inline-block; padding:2px 10px; border-radius:999px;
  color:#fff; font-weight:700; font-size:12px; letter-spacing:.04em; }
.badge.holds { background:var(--ok); }
.badge.failed { background:var(--bad); }
.badge.pending, .badge.nothing { background:var(--warn); }
.node rect { fill:var(--card); stroke:var(--line); stroke-width:1.5; }
.node.active rect { stroke:var(--active); stroke-width:2.5; }
.node.done rect { stroke:var(--ok); stroke-width:2; }
.node.bad rect { stroke:var(--bad); stroke-width:2.5; }
.node.paused rect { stroke:var(--warn); stroke-width:2.5; }
.node text { fill:var(--fg); font-size:13px; }
.node .icon { font-size:20px; }
.node .sub, .port { fill:var(--quiet); font-size:11px; }
.edge { fill:none; stroke:var(--quiet); stroke-width:1.5; opacity:.75; }
.controls { display:flex; align-items:center; gap:8px; margin-top:8px; }
.controls button { background:var(--card); color:var(--fg);
  border:1px solid var(--line); border-radius:6px; padding:2px 10px; cursor:pointer; }
.controls input[type=range] { flex:1; }
[data-phlabel] { color:var(--quiet); min-width:150px; }
.ev { padding:3px 6px; border-radius:6px; border-left:3px solid transparent; }
.ev .seq { color:var(--quiet); display:inline-block; min-width:26px; }
.ev .type { font-weight:600; }
.ev .nid { color:var(--quiet); margin-left:6px; }
.ev.current { border-left-color:var(--active); background:rgba(31,111,235,.12); }
.ev.future { opacity:.4; }
.ev.bad .type { color:var(--bad); }
.ev pre { white-space:pre-wrap; word-break:break-all; margin:4px 0 2px;
  color:var(--quiet); }
.ev details summary { cursor:pointer; color:var(--quiet); font-size:12px; }
.verdict { padding:3px 0; }
.verdict .mark { display:inline-block; min-width:18px; }
.verdict.ok .mark { color:var(--ok); }
.verdict.fail .mark { color:var(--bad); }
.seek { background:none; border:1px solid var(--line); border-radius:6px;
  color:var(--active); cursor:pointer; font-size:11px; margin-left:4px; }
.summary, .quiet, footer { color:var(--quiet); }
.pending { color:var(--warn); }
.repro-ok { color:var(--ok); }
.repro-bad { color:var(--bad); font-weight:700; }
.lint { margin:8px 0 0; padding-left:18px; }
.lint .error { color:var(--bad); }
.lint .warning { color:var(--warn); }
footer { padding:4px 16px 16px; font-size:12px; }
footer ul { margin:4px 0; padding-left:18px; }
"""

_SCRIPT = """
(function () {
  "use strict";
  function init(v) {
    var rows = Array.prototype.slice.call(v.querySelectorAll("[data-ev]"));
    var slider = v.querySelector("[data-ph]");
    if (!rows.length || !slider) { return; }
    var label = v.querySelector("[data-phlabel]");
    var groups = Array.prototype.slice.call(v.querySelectorAll("[data-gnode]"));
    var timer = null;
    function apply(idx, scroll) {
      var states = {};
      for (var k = 0; k < rows.length; k++) {
        var r = rows[k];
        r.classList.toggle("current", k === idx);
        r.classList.toggle("future", k > idx);
        if (k <= idx) {
          var t = r.getAttribute("data-type");
          var n = r.getAttribute("data-nidx");
          if (n !== "") {
            if (t === "node_started") { states[n] = "active"; }
            else if (t === "node_finished") { states[n] = "done"; }
            else if (t === "policy_violation" || t === "engine_error") { states[n] = "bad"; }
            else if (t === "run_paused") { states[n] = "paused"; }
          }
        }
      }
      for (var j = 0; j < groups.length; j++) {
        var g = groups[j];
        g.setAttribute("class", "node " + (states[g.getAttribute("data-gnode")] || "idle"));
      }
      if (idx >= 0) {
        if (scroll !== false) { rows[idx].scrollIntoView({ block: "nearest" }); }
        label.textContent = "seq " + rows[idx].getAttribute("data-seq") + " · " +
          rows[idx].getAttribute("data-type");
      } else {
        label.textContent = "start";
      }
      slider.value = String(idx);
    }
    function cur() { return parseInt(slider.value, 10); }
    function step(d) { apply(Math.max(-1, Math.min(rows.length - 1, cur() + d))); }
    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    function on(act, fn) {
      var b = v.querySelector('[data-act="' + act + '"]');
      if (b) { b.addEventListener("click", fn); }
    }
    on("first", function () { stop(); apply(-1); });
    on("prev", function () { stop(); step(-1); });
    on("next", function () { stop(); step(1); });
    on("last", function () { stop(); apply(rows.length - 1); });
    on("play", function () {
      if (timer) { stop(); return; }
      if (cur() >= rows.length - 1) { apply(-1); }
      timer = setInterval(function () {
        if (cur() >= rows.length - 1) { stop(); return; }
        step(1);
      }, 600);
    });
    slider.addEventListener("input", function () { stop(); apply(cur()); });
    var seeks = v.querySelectorAll("[data-seek]");
    for (var s = 0; s < seeks.length; s++) {
      seeks[s].addEventListener("click", function (evn) {
        stop();
        var want = evn.currentTarget.getAttribute("data-seek");
        for (var i = 0; i < rows.length; i++) {
          if (rows[i].getAttribute("data-seq") === want) { apply(i); return; }
        }
      });
    }
    apply(rows.length - 1, false);
  }
  var vs = document.querySelectorAll("[data-viewer]");
  for (var i = 0; i < vs.length; i++) { init(vs[i]); }
})();
"""

_PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$TITLE</title>
<style>$STYLE</style>
</head>
<body>
<header>$HEADER</header>
<main>
$BODY
</main>
<footer>$FOOTER</footer>
<script type="application/json" id="evarness-data">$DATA</script>
<script>$SCRIPT</script>
</body>
</html>
""")


@register_renderer("html")
def render_html(subject: RenderSubject) -> str:
    """The built-in renderer: canvas + playhead + evidence + judgment, one file."""
    digest = trace_digest(subject.events) if subject.events is not None else None
    node_index = {n.id: i for i, n in enumerate(subject.graph.nodes)}
    if subject.events is not None:
        controls = _controls(len(subject.events))
        evidence = _event_rows(subject.events, node_index)
        status = subject.meta.get("status")
    else:
        controls = '<span class="quiet">static render — no run attached</span>'
        evidence = '<p class="quiet">No run attached — this is the graph’s shape, not evidence.</p>'
        status = None
    judgment = _judgment_block(subject.verdicts, list(subject.graph.params.invariants), status)

    lines = [
        "This artifact is derived evidence: a view of the canonical trace, not the "
        "contract itself. It does not establish that the run happened on any "
        "particular machine or at any particular time."
    ]
    if subject.events is None:
        lines.append("No run is attached: this shows the graph’s shape, not its behavior.")
    else:
        if subject.meta.get("deterministic") is False:
            lines.append(
                "The run declared deterministic: false — the digest identifies this "
                "trace but is not expected to reproduce."
            )
        if subject.meta.get("status") == "paused":
            lines.append(
                "The run paused at a human gate: declared invariants were never "
                "evaluated, and no judgment should be inferred from the pause."
            )
        if not subject.graph.params.invariants:
            lines.append("No invariants were declared — nothing was asserted.")

    return _PAGE.substitute(
        TITLE=escape(subject.graph.name or subject.graph.id),
        STYLE=_STYLE,
        HEADER=_provenance(subject, digest) + _lint_strip(subject.meta),
        BODY=_viewer(
            _canvas_svg(subject.graph, subject.presentation, node_index),
            controls,
            evidence,
            judgment,
        ),
        FOOTER=_footer(lines),
        DATA=_subject_island(subject, digest),
        SCRIPT=_SCRIPT,
    )


# ---------------------------------------------------------------- proof browser

_BADGES: dict = {  # keyed by verdict.ok, plus the zero-contract override
    "nothing": ("NOTHING ASSERTED", "nothing"),
    True: ("PROOF HOLDS", "holds"),
    None: ("PROOF PENDING", "pending"),
    False: ("PROOF FAILED", "failed"),
}


def render_proof_browser(
    bundle: dict,
    graph: GraphModel | None = None,
    presentation: dict[str, dict] | None = None,
) -> str:
    """A proof bundle as one browsable page: the tri-state verdict badge, then
    one viewer per scenario (canvas + playhead when the bundle embeds events
    and ``graph`` matches the pinned subject), the bundle's ``not_proven``
    section verbatim, and the whole bundle in the data island — extract it and
    ``evarness verify`` re-checks it, signature included."""
    subject = bundle.get("subject") or {}
    verdict = bundle.get("verdict") or {}
    declared = list(subject.get("invariants_declared") or [])
    presentation = presentation or {}

    if graph is not None and graph_hash(graph) != subject.get("graph_sha256"):
        raise RenderMismatchError(
            "graph does not match the bundle's pinned subject "
            f"(graph_sha256 {str(subject.get('graph_sha256'))[:16]}…) — "
            "the canvas is only ever drawn from the proven graph"
        )
    node_index = {n.id: i for i, n in enumerate(graph.nodes)} if graph is not None else {}

    badge_text, badge_cls = _BADGES["nothing"] if not declared else _BADGES[verdict.get("ok")]
    name = subject.get("graph_name") or subject.get("graph_id") or "proof"
    bits = [
        f'<span class="badge {badge_cls}">{escape(badge_text)}</span>',
        f"<strong>{escape(str(name))}</strong>",
    ]
    for label, key in (("pattern", "pattern"), ("seed", "seed"), ("provider", "provider")):
        if subject.get(key) is not None:
            bits.append(f"{label} {escape(str(subject[key]))}")
    bits.append(f'graph <code>{escape(str(subject.get("graph_sha256"))[:16])}…</code>')
    engine = bundle.get("engine") or {}
    bits.append(
        f'proof {escape(str(bundle.get("proof_version")))} · '
        f'evarness {escape(str(engine.get("evarness")))} · renderer {RENDER_VERSION}'
    )
    if bundle.get("attestation"):
        bits.append(
            '<span class="pending">signed — signature NOT checked by this page; '
            "run evarness verify --require-signature</span>"
        )

    viewers = []
    for sc in bundle.get("scenarios", []):
        repro = {
            True: '<span class="repro-ok">✓ digest reproduced</span>',
            False: '<span class="repro-bad">✗ DIGEST DID NOT REPRODUCE</span>',
            None: '<span class="quiet">reproduction not attempted</span>',
        }[sc.get("reproduced")]
        head = (
            f'<div class="head"><strong>{escape(sc.get("fixture", ""))}</strong> · '
            f'status {escape(sc.get("status", ""))} · '
            f'deterministic {str(bool(sc.get("deterministic"))).lower()} · {repro} · '
            f'digest <code>{escape(sc.get("trace_digest", ""))}</code> · '
            f'{int(sc.get("events_count", 0))} events</div>'
        )
        events = sc.get("events")
        if graph is not None:
            canvas = _canvas_svg(graph, presentation, node_index)
        else:
            canvas = (
                '<p class="quiet">Canvas omitted: the bundle pins the graph by hash '
                "only and no matching graph was provided.</p>"
            )
        if events is not None:
            controls = _controls(len(events))
            evidence = _event_rows(events, node_index)
        else:
            controls = '<span class="quiet">no playhead — events not embedded</span>'
            evidence = (
                '<p class="quiet">Canonical events were omitted from this bundle '
                f'(--no-events); {int(sc.get("events_count", 0))} events are named by '
                "the digest but not replayable here.</p>"
            )
        judgment = _judgment_block(sc.get("invariants"), declared, sc.get("status"))
        viewers.append(_viewer(canvas, controls, evidence, judgment, head=head))

    lines = list(bundle.get("not_proven") or [])

    meta = {
        "artifact": "proof-browser",
        "render_version": RENDER_VERSION,
        "graph_attached": graph is not None,
    }
    island = _island(['"meta":' + _json_compact(meta), '"bundle":' + _json_compact(bundle)])

    return _PAGE.substitute(
        TITLE=escape(f"proof · {name}"),
        STYLE=_STYLE,
        HEADER=" · ".join(bits),
        BODY="\n".join(viewers) or '<p class="quiet">The bundle contains no scenarios.</p>',
        FOOTER=_footer(lines, title="What this bundle does not prove"),
        DATA=island,
        SCRIPT=_SCRIPT,
    )
