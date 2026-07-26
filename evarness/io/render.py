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

RENDER_VERSION = "r3"

_FALLBACK_PRESENTATION = {"icon": "⬡", "label": None}
_FALLBACK_GROUP_COLOR = "#7aa2f7"  # any group the presentation table doesn't name

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

# Node card dimensions match the canvas the packaged patterns were authored
# on — authored positions step ~180px horizontally, so a wider card would
# make adjacent nodes touch (found live: r2's 190px cards rendered the
# flagship as a strip of abutting boxes, not a graph).
NODE_W, NODE_H = 150, 52
GAP_X, GAP_Y, MARGIN = 200, 84, 30


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


def _edge_d(a: tuple[int, int], b: tuple[int, int]) -> str:
    """Edge geometry, port to port. Long same-row edges arc over the nodes
    between them (fan-out stays readable); stacked nodes connect vertically;
    everything else is a straight port-to-port line with an arrowhead."""
    ax, ay = a
    bx, by = b
    if ay == by and bx - ax > NODE_W + 60:
        return (
            f"M {ax + NODE_W // 2} {ay} "
            f"Q {(ax + bx + NODE_W) // 2} {ay - 46} {bx + NODE_W // 2} {by}"
        )
    if abs(ax - bx) < 20:
        return f"M {ax + NODE_W // 2} {ay + NODE_H} L {bx + NODE_W // 2} {by}"
    if bx > ax:
        return f"M {ax + NODE_W} {ay + NODE_H // 2} L {bx} {by + NODE_H // 2}"
    return f"M {ax} {ay + NODE_H // 2} L {bx + NODE_W} {by + NODE_H // 2}"


def _canvas_svg(
    graph: GraphModel,
    presentation: dict,
    node_index: dict[str, int],
    marker_id: str = "arr",
) -> str:
    pos = layered_layout(graph)
    width = max((x for x, _ in pos.values()), default=0) + NODE_W + MARGIN
    height = max((y for _, y in pos.values()), default=0) + NODE_H + MARGIN
    parts: list[str] = [
        f'<defs><marker id="{escape(marker_id)}" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0L10,5L0,10z" class="arrow"/></marker></defs>'
    ]
    for e in graph.edges:
        if e.from_ not in pos or e.to not in pos:
            continue
        parts.append(
            f'<path class="edge" data-eto="{node_index[e.to]}" '
            f'd="{_edge_d(pos[e.from_], pos[e.to])}" '
            f'marker-end="url(#{escape(marker_id)})"/>'
        )
        if e.from_port != "out" or e.to_port != "in":
            (ax, ay), (bx, by) = pos[e.from_], pos[e.to]
            mx, my = (ax + bx + NODE_W) // 2, (ay + by + NODE_H) // 2 - 8
            parts.append(
                f'<text class="portlbl" x="{mx}" y="{my}">'
                f"{escape(e.from_port)}→{escape(e.to_port)}</text>"
            )
    for n in graph.nodes:
        if n.id not in pos:
            continue
        x, y = pos[n.id]
        p = presentation.get(n.type) or _FALLBACK_PRESENTATION
        label = str(n.label or p.get("label") or n.type)
        if len(label) > 20:
            label = label[:19] + "…"
        color = p.get("color") or _FALLBACK_GROUP_COLOR
        parts.append(
            f'<g class="node idle" data-gnode="{node_index[n.id]}" '
            f'transform="translate({x},{y})">'
            f'<rect width="{NODE_W}" height="{NODE_H}" rx="9"/>'
            f'<circle cx="14" cy="{NODE_H // 2}" r="4.5" fill="{escape(color)}"/>'
            f'<text class="nid-badge" x="{NODE_W - 6}" y="12" text-anchor="end">'
            f"{escape(n.id)}</text>"
            f'<text class="label" x="27" y="22">{escape(label)}</text>'
            f'<text class="typ" x="27" y="38">{escape(n.type)}</text>'
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


def _badge(text: str, cls: str) -> str:
    return f'<span class="badge {cls}">{escape(text)}</span>'


# run statuses reuse the verdict badge palette: green good, red stopped,
# orange human-pending, faint for a canvas with no run attached
_STATUS_BADGES = {
    "completed": ("COMPLETED", "holds"),
    "blocked": ("BLOCKED", "failed"),
    "failed": ("FAILED", "failed"),
    "paused": ("PAUSED", "pending"),
}


def _stat(label: str, value: str, code: bool = False) -> str:
    v = f"<code>{escape(value)}</code>" if code else escape(value)
    return (
        f'<div class="stat"><span class="stat-l">{escape(label)}</span>'
        f'<span class="stat-v">{v}</span></div>'
    )


def _masthead(badge_html: str, title: str, subtitle_html: str, stats_html: str) -> str:
    """The page header: badge + title block on the left, labeled stat chips
    on the right; the digest gets its own full-width strip below (75 chars
    of hash never fits politely on a chip)."""
    return (
        f'<div class="masthead"><div class="title">{badge_html}'
        f"<div><h1>{escape(title)}</h1>"
        f'<div class="subtitle">{subtitle_html}</div></div></div>'
        f'<div class="stats">{stats_html}</div></div>'
    )


def _digestbar(digest: str) -> str:
    return (
        f'<div class="digestbar"><span class="stat-l">digest</span>'
        f"<code>{escape(digest)}</code></div>"
    )


def _run_header(subject: RenderSubject, digest: str | None) -> str:
    g, meta = subject.graph, subject.meta
    if subject.events is None:
        badge = _badge("STATIC", "static")
        sub_bits = ["graph only — no run attached"]
    else:
        text, cls = _STATUS_BADGES.get(
            str(meta.get("status")), (str(meta.get("status", "run")).upper(), "static")
        )
        badge = _badge(text, cls)
        sub_bits = []
        if meta.get("scenario"):
            sub_bits.append(f"scenario {escape(str(meta['scenario']))}")
        if meta.get("reason"):
            sub_bits.append(f'<span class="reason">{escape(str(meta["reason"]))}</span>')
    stats = []
    if meta.get("provider"):
        stats.append(_stat("provider", str(meta["provider"]), code=True))
    stats.append(_stat("seed", str(g.params.seed)))
    if meta.get("deterministic") is not None:
        stats.append(_stat("deterministic", str(bool(meta["deterministic"])).lower()))
    stats.append(_stat("renderer", RENDER_VERSION))
    header = _masthead(badge, g.name or g.id, " · ".join(sub_bits), "".join(stats))
    if digest:
        header += _digestbar(digest)
    return header + _lint_strip(meta)


_STYLE = """
:root { color-scheme: dark;
  --bg:#0d1017; --bg2:#131722; --bg3:#1a2030; --line:#252c3f;
  --txt:#dde3f0; --dim:#8b93a7; --faint:#5c6478;
  --blue:#7aa2f7; --red:#f7768e; --green:#9ece6a; --orange:#e0af68;
  --purple:#bb9af7; --cyan:#7dcfff; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--txt);
  font:14px/1.5 -apple-system, 'Segoe UI', Roboto, sans-serif; }
header { padding:12px 18px 10px; border-bottom:1px solid var(--line);
  background:var(--bg2); font-size:13px; }
header code, .head code { background:var(--bg3); padding:1px 6px;
  border-radius:4px; font-size:11.5px; }
.masthead { display:flex; justify-content:space-between; align-items:center;
  gap:18px; flex-wrap:wrap; }
.title { display:flex; align-items:center; gap:12px; min-width:0; }
.title h1 { font-size:15px; font-weight:700; margin:0; line-height:1.25; }
.subtitle { font-size:11.5px; color:var(--dim); margin-top:1px; }
.subtitle .reason { color:var(--red); }
.stats { display:flex; gap:16px; flex-wrap:wrap; align-items:center; }
.stat { display:flex; flex-direction:column; gap:1px; }
.stat-l { font-size:9px; text-transform:uppercase; letter-spacing:.09em;
  color:var(--faint); }
.stat-v { font-size:12px; color:var(--txt);
  font-variant-numeric:tabular-nums; white-space:nowrap; }
.digestbar { margin-top:9px; padding-top:8px; border-top:1px solid var(--line);
  display:flex; align-items:baseline; gap:10px; }
.digestbar code { font-size:11.5px; word-break:break-all; }
.warnstrip { margin-top:9px; padding:6px 11px; border:1px solid var(--orange);
  border-radius:8px; color:var(--orange); font-size:12px;
  background:rgba(224,175,104,.07); }
main { padding:16px 18px; }
.viewer { display:grid; grid-template-columns: minmax(0,1fr) 380px; gap:14px;
  margin-bottom:18px; }
.viewer .head { grid-column: 1 / -1; padding:7px 12px; border:1px solid
  var(--line); border-radius:9px; background:var(--bg2); font-size:12.5px;
  display:flex; justify-content:space-between; align-items:center; gap:12px;
  flex-wrap:wrap; }
.head-l, .head-r { display:flex; align-items:center; gap:10px; min-width:0; }
.head-r { color:var(--dim); }
.panel { background:var(--bg2); border:1px solid var(--line); border-radius:10px;
  padding:12px; overflow:auto; }
.canvas { overflow:auto; }
.side { display:flex; flex-direction:column; gap:14px; min-width:0; }
.side .panel { max-height:44vh; }
h2 { font-size:11px; text-transform:uppercase; letter-spacing:.08em;
  color:var(--faint); margin:0 0 8px; }
.badge { display:inline-block; padding:2px 10px; border-radius:99px;
  font-weight:700; font-size:11px; letter-spacing:.05em; border:1px solid; }
.badge.holds { color:var(--green); border-color:var(--green); }
.badge.failed { color:var(--red); border-color:var(--red); }
.badge.pending, .badge.nothing { color:var(--orange); border-color:var(--orange); }
/* canvas nodes: dim until the playhead reaches them, glow while active */
.node rect { fill:var(--bg3); stroke:var(--line); stroke-width:1.4; }
.node.idle { opacity:.5; }
.node.active rect { stroke:var(--orange); stroke-width:2.5;
  filter:drop-shadow(0 0 7px rgba(224,175,104,.7)); }
.node.done rect { stroke:var(--green); stroke-width:1.6; }
.node.bad rect { stroke:var(--red); stroke-width:2.5;
  filter:drop-shadow(0 0 7px rgba(247,118,142,.6)); }
.node.paused rect { stroke:var(--orange); stroke-width:2.2;
  stroke-dasharray:6 4; }
.node .label { fill:var(--txt); font-size:12px; font-weight:600; }
.node .typ { fill:var(--faint); font-size:9.5px; }
.node .nid-badge { fill:var(--faint); font-size:8.5px; }
.node.active .nid-badge { fill:var(--orange); }
.portlbl { fill:var(--cyan); font-size:9px; }
.edge { stroke:#3a4460; stroke-width:1.6; fill:none; }
.edge.flow { stroke:var(--orange); stroke-width:2.4; }
.arrow { fill:#3a4460; }
.controls { display:flex; align-items:center; gap:8px; margin-top:10px; }
.controls button { background:var(--bg3); color:var(--txt);
  border:1px solid var(--line); border-radius:8px; padding:3px 12px;
  cursor:pointer; font-size:12.5px; }
.controls button:hover { border-color:var(--cyan); }
.controls input[type=range] { flex:1; accent-color:var(--cyan); }
[data-phlabel] { color:var(--dim); min-width:150px; font-size:12px;
  font-variant-numeric:tabular-nums; }
.ev { padding:3px 7px; border-radius:6px; border-left:3px solid transparent;
  font-size:12.5px; }
.ev .seq { color:var(--faint); display:inline-block; min-width:26px;
  font-variant-numeric:tabular-nums; }
.ev .type { font-weight:600; }
.ev .nid { color:var(--faint); margin-left:6px; font-size:11px; }
.ev.current { border-left-color:var(--cyan); background:var(--bg3); }
.ev.future { opacity:.35; }
.ev.bad .type { color:var(--red); }
.ev pre { white-space:pre-wrap; word-break:break-all; margin:4px 0 2px;
  color:var(--dim); font-size:11px; }
.ev details summary { cursor:pointer; color:var(--faint); font-size:11px; }
.verdict { padding:3px 0; font-size:12.5px; }
.verdict .mark { display:inline-block; min-width:18px; }
.verdict.ok .mark { color:var(--green); }
.verdict.fail .mark { color:var(--red); }
.seek { background:none; border:1px solid var(--line); border-radius:6px;
  color:var(--cyan); cursor:pointer; font-size:11px; margin-left:4px; }
.seek:hover { border-color:var(--cyan); }
.summary, .quiet, footer { color:var(--dim); }
.pending { color:var(--orange); }
.repro-ok { color:var(--green); }
.repro-bad { color:var(--red); font-weight:700; }
.lint { margin:8px 0 0; padding-left:18px; font-size:12px; }
.lint .error { color:var(--red); }
.lint .warning { color:var(--orange); }
footer { padding:6px 18px 18px; font-size:12px; }
footer ul { margin:4px 0; padding-left:18px; }
footer h2 { margin-top:8px; }
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
    var edges = Array.prototype.slice.call(v.querySelectorAll("[data-eto]"));
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
      // data flowed: edges into the currently-active node light up
      for (var m = 0; m < edges.length; m++) {
        var into = states[edges[m].getAttribute("data-eto")] === "active";
        edges[m].setAttribute("class", into ? "edge flow" : "edge");
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
        HEADER=_run_header(subject, digest),
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
    scenarios = bundle.get("scenarios", [])
    sub_bits = []
    if subject.get("pattern"):
        sub_bits.append(f"pattern {escape(str(subject['pattern']))}")
    sub_bits.append(f"{len(scenarios)} scenario(s)")
    engine = bundle.get("engine") or {}
    stats = []
    if subject.get("provider") is not None:
        stats.append(_stat("provider", str(subject["provider"]), code=True))
    if subject.get("seed") is not None:
        stats.append(_stat("seed", str(subject["seed"])))
    stats.append(_stat("graph", str(subject.get("graph_sha256"))[:16] + "…", code=True))
    stats.append(_stat("proof", str(bundle.get("proof_version"))))
    stats.append(_stat("evarness", str(engine.get("evarness"))))
    stats.append(_stat("renderer", RENDER_VERSION))
    header = _masthead(
        _badge(badge_text, badge_cls), str(name), " · ".join(sub_bits), "".join(stats)
    )
    if bundle.get("attestation"):
        header += (
            '<div class="warnstrip">signed — signature NOT checked by this page; '
            "run evarness verify --require-signature</div>"
        )

    viewers = []
    for i, sc in enumerate(bundle.get("scenarios", [])):
        repro = {
            True: '<span class="repro-ok">✓ digest reproduced</span>',
            False: '<span class="repro-bad">✗ DIGEST DID NOT REPRODUCE</span>',
            None: '<span class="quiet">reproduction not attempted</span>',
        }[sc.get("reproduced")]
        st_text, st_cls = _STATUS_BADGES.get(
            str(sc.get("status")), (str(sc.get("status", "")).upper(), "static")
        )
        head = (
            f'<div class="head"><div class="head-l">'
            f'<strong>{escape(sc.get("fixture", ""))}</strong>'
            f"{_badge(st_text, st_cls)}"
            f'<span class="quiet">deterministic '
            f'{str(bool(sc.get("deterministic"))).lower()}</span>{repro}</div>'
            f'<div class="head-r">digest <code>{escape(sc.get("trace_digest", ""))}</code>'
            f'<span class="quiet">{int(sc.get("events_count", 0))} events</span></div></div>'
        )
        events = sc.get("events")
        if graph is not None:
            # unique marker id per viewer: duplicate SVG defs ids are invalid HTML
            canvas = _canvas_svg(graph, presentation, node_index, marker_id=f"arr{i}")
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
        HEADER=header,
        BODY="\n".join(viewers) or '<p class="quiet">The bundle contains no scenarios.</p>',
        FOOTER=_footer(lines, title="What this bundle does not prove"),
        DATA=island,
        SCRIPT=_SCRIPT,
    )
