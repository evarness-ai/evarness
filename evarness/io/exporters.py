"""Trace exporters — canonical event streams in formats the rest of the world reads.

Evarness's native trace is the canonical event stream (trace.py). This
module projects it into standard interchange formats so a run recorded here can
land in existing pipelines instead of an observability island:

  * ``jsonl`` — the native form: one canonical event per line, byte-stable
    (sorted keys, compact, ascii). The digest input, line by line.
  * ``otlp``  — an OpenTelemetry OTLP/JSON document (``resourceSpans``): the run
    is the root span, each node execution is a child span, every other engine
    event rides as a span event on its node's span. GenAI semantic-convention
    attribute names (``gen_ai.provider.name``, ``gen_ai.request.model``,
    ``gen_ai.usage.total_tokens``) are used where the mapping is exact;
    everything Evarness-specific is namespaced ``evarness.*`` — no
    pretending our vocabulary is theirs.

Exports are DERIVED evidence, not the contract: they include wall-clock
timestamps and are not expected to be byte-identical across runs. The canonical
digest travels inside every export (``evarness.trace_digest``) so a consumer
can always name the underlying trace. Trace/span ids are content-derived
(sha256 of digest + seq) — stable for a deterministic run, and honest about
being identifiers, not randomness.

Extension point: register your own format by name —

    from evarness.io.exporters import register_exporter

    @register_exporter("csv", media_type="text/csv", extension=".csv")
    def export_csv(events, meta, cfg):
        ...return the serialized document as a str...

Python-plugin exporters arrive with the Tools SDK in a later release. Knobs
live in packaged ``exporters.yaml`` with a
per-format user overlay at ``~/.evarness/exporters.yaml``
(``$EVARNESS_EXPORTERS``). An unknown format is a loud ``ValueError`` naming
the formats that do exist — never a silent fallback.

JUnit and SARIF are deliberately NOT here: they report *verdicts*, not traces,
so they render proof bundles (prove.py).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

import yaml

from evarness.core.errors import EvarnessError
from evarness.core.trace import canonical_event, trace_digest

_PACKAGED = Path(__file__).parent / "exporters.yaml"


class ExportFormatError(EvarnessError, ValueError):
    """An unknown trace format was requested. Still a ValueError for existing
    callers; also part of the EvarnessError family so the CLI renders it as a
    message, not a traceback (E9)."""


def _overlay_path() -> Path:
    return Path(
        os.environ.get("EVARNESS_EXPORTERS", str(Path.home() / ".evarness" / "exporters.yaml"))
    )


_cfg_cache: dict | None = None


def exporter_config(force_reload: bool = False) -> dict:
    """Packaged exporters.yaml with the user overlay merged per format."""
    global _cfg_cache
    if _cfg_cache is None or force_reload:
        cfg = yaml.safe_load(_PACKAGED.read_text()) or {}
        overlay = _overlay_path()
        if overlay.is_file():
            user = yaml.safe_load(overlay.read_text()) or {}
            for fmt, knobs in user.items():
                merged = dict(cfg.get(fmt) or {})
                merged.update(knobs or {})
                cfg[fmt] = merged
        _cfg_cache = cfg
    return _cfg_cache


def _engine_version() -> str:
    try:
        return metadata.version("evarness")
    except metadata.PackageNotFoundError:  # bare checkout
        return "unknown"


# ------------------------------------------------------------------- registry


@dataclass(frozen=True)
class Exporter:
    name: str
    fn: Callable[[list[dict], dict, dict], str]
    media_type: str
    extension: str


EXPORTERS: dict[str, Exporter] = {}


def register_exporter(name: str, media_type: str = "application/json", extension: str = ".json"):
    """Register a trace exporter by name. ``fn(events, meta, cfg) -> str`` where
    ``events`` is the raw engine stream, ``meta`` is run context (run_id, name,
    status, seed, provider, deterministic — whatever the caller knows), and
    ``cfg`` is this format's section from exporters.yaml (+ overlay)."""

    def deco(fn):
        EXPORTERS[name] = Exporter(name=name, fn=fn, media_type=media_type, extension=extension)
        return fn

    return deco


def export_formats() -> list[str]:
    _load_plugin_exporters()
    return sorted(EXPORTERS)


def export_formats_meta() -> list[dict]:
    """The registered formats with their presentation facts — what a UI needs to
    offer a download control without hardcoding the format list."""
    _load_plugin_exporters()
    return [
        {"id": e.name, "media_type": e.media_type, "extension": e.extension}
        for _, e in sorted(EXPORTERS.items())
    ]


def export_trace(fmt: str, events: list[dict], meta: dict | None = None) -> tuple[str, str]:
    """Serialize ``events`` in ``fmt``. Returns ``(document, media_type)``.
    Unknown formats raise — misconfiguration is loud, never a silent fallback."""
    _load_plugin_exporters()
    exp = EXPORTERS.get(fmt)
    if exp is None:
        raise ExportFormatError(
            f"unknown trace format '{fmt}' — available: " f"{', '.join(sorted(EXPORTERS))}"
        )
    cfg = exporter_config().get(fmt) or {}
    return exp.fn(events, dict(meta or {}), cfg), exp.media_type


def _load_plugin_exporters() -> None:
    """Python-plugin exporters arrive with the Tools SDK in a later release;
    until then only the built-ins and the YAML overlay are available. This
    hook stays so the registry shape doesn't change when plugins land."""
    return None


# -------------------------------------------------------------------- builtins


@register_exporter("jsonl", media_type="application/x-ndjson", extension=".jsonl")
def export_jsonl(events: list[dict], meta: dict, cfg: dict) -> str:
    """Native canonical trace, one event per line — exactly the bytes the
    digest is computed over, split at event boundaries."""
    return (
        "\n".join(
            json.dumps(canonical_event(e), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            for e in events
        )
        + "\n"
    )


def _hex_id(seedtext: str, nbytes: int) -> str:
    return hashlib.sha256(seedtext.encode("utf-8")).hexdigest()[: nbytes * 2]


def _nanos(ts: float | None) -> str:
    # OTLP/JSON encodes uint64 as a decimal string
    return str(int(round((ts or 0.0) * 1e9)))


def _attr(key: str, value: Any) -> dict:
    if isinstance(value, bool):
        v: dict = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}  # int64 is a string in OTLP/JSON
    elif isinstance(value, float):
        v = {"doubleValue": value}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}


def _provider_attrs(provider: str | None) -> list[dict]:
    """'ollama:qwen2.5:7b' -> gen_ai.provider.name=ollama, request.model=rest;
    'sim:helpful-v1'/'sim' stays a provider named sim — honestly simulated."""
    if not provider:
        return []
    system, _, model = str(provider).partition(":")
    out = [_attr("gen_ai.provider.name", system)]
    if model:
        out.append(_attr("gen_ai.request.model", model))
    return out


@register_exporter("otlp", media_type="application/json", extension=".otlp.json")
def export_otlp(events: list[dict], meta: dict, cfg: dict) -> str:
    """One OTLP/JSON document: root span = the run, child span = each node
    execution (node_started..node_finished), span events = everything else,
    attached to the node span they belong to (falling back to the root)."""
    digest = meta.get("trace_digest") or trace_digest(events)
    trace_id = _hex_id(f"{digest}", 16)
    root_id = _hex_id(f"{digest}:root", 8)

    started = next((e for e in events if e["type"] == "run_started"), None)
    finished = next(
        (e for e in events if e["type"] in ("run_finished", "run_failed", "run_paused")), None
    )
    t0 = events[0]["ts"] if events else 0.0
    t1 = events[-1]["ts"] if events else t0

    status = meta.get("status")
    root_status: dict = {}
    if status == "completed":
        root_status = {"code": "STATUS_CODE_OK"}
    elif status in ("blocked", "failed"):
        root_status = {"code": "STATUS_CODE_ERROR", "message": str(meta.get("reason") or status)}

    root_attrs = [_attr("evarness.trace_digest", digest)]
    for key in ("run_id", "status", "seed", "fixture", "pattern"):
        if meta.get(key) is not None:
            root_attrs.append(_attr(f"evarness.{key}", meta[key]))
    if started:
        root_attrs.append(
            _attr("evarness.deterministic", bool(started["payload"].get("deterministic")))
        )
        root_attrs.extend(
            _provider_attrs(started["payload"].get("provider") or meta.get("provider"))
        )
    if finished and finished["type"] == "run_finished":
        root_attrs.append(
            _attr("gen_ai.usage.total_tokens", int(finished["payload"].get("total_tokens") or 0))
        )

    def span_event(e: dict) -> dict:
        attrs = [
            _attr("evarness.seq", e["seq"]),
            _attr(
                "evarness.payload",
                json.dumps(e["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=True),
            ),
        ]
        return {"timeUnixNano": _nanos(e["ts"]), "name": e["type"], "attributes": attrs}

    spans = []
    open_span: dict | None = None  # engine executes nodes sequentially
    root_events: list[dict] = []
    for e in events:
        if e["type"] == "node_started":
            open_span = {
                "traceId": trace_id,
                "spanId": _hex_id(f"{digest}:{e['seq']}", 8),
                "parentSpanId": root_id,
                "name": f"{e['payload'].get('type', 'node')}:{e['node_id']}",
                "kind": "SPAN_KIND_INTERNAL",
                "startTimeUnixNano": _nanos(e["ts"]),
                "endTimeUnixNano": _nanos(e["ts"]),  # patched on node_finished
                "attributes": [
                    _attr("evarness.node_id", e["node_id"] or ""),
                    _attr("evarness.node_type", e["payload"].get("type", "")),
                ],
                "events": [],
                "status": {},
            }
            spans.append(open_span)
        elif e["type"] == "node_finished" and open_span is not None:
            open_span["endTimeUnixNano"] = _nanos(e["ts"])
            open_span = None
        elif open_span is not None:
            # a block/pause aborts the node mid-flight — the event still lands
            # on the span that was executing, and the span stays honest about
            # never finishing (end = last event seen inside it)
            open_span["events"].append(span_event(e))
            open_span["endTimeUnixNano"] = _nanos(e["ts"])
            if e["type"] == "policy_violation":
                open_span["status"] = {
                    "code": "STATUS_CODE_ERROR",
                    "message": str(e["payload"].get("reason") or ""),
                }
        else:
            root_events.append(span_event(e))

    root_span = {
        "traceId": trace_id,
        "spanId": root_id,
        "name": f"evarness.run:{meta.get('name') or meta.get('graph_id') or 'graph'}",
        "kind": "SPAN_KIND_INTERNAL",
        "startTimeUnixNano": _nanos(t0),
        "endTimeUnixNano": _nanos(t1),
        "attributes": root_attrs,
        "events": root_events,
        "status": root_status,
    }

    resource_attrs = [_attr("service.name", cfg.get("service_name", "evarness"))]
    for k, v in (cfg.get("resource_attributes") or {}).items():
        resource_attrs.append(_attr(str(k), v))

    doc = {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeSpans": [
                    {
                        "scope": {"name": "evarness", "version": _engine_version()},
                        "spans": [root_span] + spans,
                    }
                ],
            }
        ]
    }
    return json.dumps(doc, indent=2)
