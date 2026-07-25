"""Pattern library: built-ins (this package) + user-published patterns
(`~/.evarness/patterns/`, override with EVARNESS_PATTERNS).

Each pattern = graph.json + fixtures/*.yaml + lesson.md. A `.harness` bundle is a
zip of exactly that directory shape (DR6) — publish, export, and import all speak
the same layout. Every pattern must ship at least one fixture — the honesty rule
is enforced content policy, not decoration.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path

import yaml

from evarness.core.registry import NODE_TYPES as REGISTRY
from evarness.domains.agents import nodes as _nodes  # noqa: F401  (registers node types)
from evarness.core.graph import GraphModel, lint, migrate
from evarness.domains.agents.sim import Fixture

PATTERNS_DIR = Path(__file__).parent
PATTERN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,63}$")


def user_patterns_dir() -> Path:
    env = os.environ.get("EVARNESS_PATTERNS")
    return Path(env) if env else Path.home() / ".evarness" / "patterns"


def builtin_ids() -> set[str]:
    return {d.name for d in PATTERNS_DIR.iterdir() if d.is_dir() and (d / "graph.json").exists()}


def _pattern_dir(pattern_id: str) -> Path | None:
    for base in (PATTERNS_DIR, user_patterns_dir()):
        d = base / pattern_id
        if (d / "graph.json").exists():
            return d
    return None


def _summarize(d: Path, source: str) -> dict:
    graph = json.loads((d / "graph.json").read_text())
    fixtures = (
        sorted(p.stem for p in (d / "fixtures").glob("*.yaml")) if (d / "fixtures").exists() else []
    )
    lesson = (d / "lesson.md").read_text() if (d / "lesson.md").exists() else ""
    category = (graph.get("metadata") or {}).get("category") or "Uncategorized"
    return {
        "id": d.name,
        "name": graph.get("name", d.name),
        "description": graph.get("description", ""),
        "category": category,
        "fixtures": fixtures,
        "lesson": lesson,
        "source": source,
    }


def list_patterns() -> list[dict]:
    out = []
    for d in sorted(PATTERNS_DIR.iterdir()):
        if d.is_dir() and (d / "graph.json").exists():
            out.append(_summarize(d, "builtin"))
    user_dir = user_patterns_dir()
    if user_dir.exists():
        for d in sorted(user_dir.iterdir()):
            if d.is_dir() and (d / "graph.json").exists():
                out.append(_summarize(d, "user"))
    return out


def load_pattern(pattern_id: str) -> dict | None:
    d = _pattern_dir(pattern_id)
    return json.loads((d / "graph.json").read_text()) if d else None


def invariant_defs(pattern_id: str) -> dict:
    """Pattern-local invariant contracts: an invariants.yaml sitting next
    to the pattern's graph.json. Highest-precedence definitions — they win over
    the user overlay and the packaged library. Empty dict when absent."""
    d = _pattern_dir(pattern_id)
    f = d / "invariants.yaml" if d else None
    if not f or not f.exists():
        return {}
    doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return doc.get("invariants") or {}


def fixture_names(pattern_id: str) -> list[str]:
    """All fixture names a pattern ships, sorted."""
    d = _pattern_dir(pattern_id)
    if not d:
        return []
    return sorted(f.stem for f in (d / "fixtures").glob("*.yaml"))


def fixture_path(pattern_id: str, fixture_name: str) -> Path | None:
    d = _pattern_dir(pattern_id)
    if not d:
        return None
    p = d / "fixtures" / f"{fixture_name}.yaml"
    return p if p.exists() else None


def fixture_text(pattern_id: str, fixture_name: str) -> str | None:
    p = fixture_path(pattern_id, fixture_name)
    return p.read_text() if p else None


# ---------------------------------------------------------------- publish (Pattern Studio)


def validate_fixture_yaml(text: str) -> dict:
    """A fixture must parse as a YAML mapping and construct a Fixture."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("fixture YAML must be a mapping (scenario, user_input, ...)")
    Fixture(doc)  # constructor applies the schema defaults; raises on wrong shapes
    return doc


def publish_pattern(
    pattern_id: str,
    graph_doc: dict,
    lesson: str,
    fixtures: dict[str, str],
    category: str = "",
    invariants_yaml: str = "",
) -> dict:
    """Promote a graph into a user pattern: validate everything, then write the
    pattern directory. Republishing the same id overwrites (user patterns only)."""
    if not PATTERN_ID_RE.match(pattern_id or ""):
        raise ValueError(
            "pattern id must be a slug: lowercase letters, digits, " "underscores (2-64 chars)"
        )
    if pattern_id in builtin_ids():
        raise ValueError(f"'{pattern_id}' is a built-in pattern — pick another id")
    if not fixtures:
        raise ValueError("a pattern must ship at least one fixture (honesty rule)")
    if category.strip():
        graph_doc = {
            **graph_doc,
            "metadata": {**(graph_doc.get("metadata") or {}), "category": category.strip()},
        }
    graph = GraphModel.model_validate(migrate(graph_doc))
    errors = [i for i in lint(graph, REGISTRY) if i["level"] == "error"]
    if errors:
        raise ValueError("graph has lint errors: " + "; ".join(i["message"] for i in errors))
    for name, text in fixtures.items():
        if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", name):
            raise ValueError(f"fixture name '{name}' must be a slug")
        try:
            validate_fixture_yaml(text)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"fixture '{name}' is not valid YAML: {exc}")
    if invariants_yaml.strip():
        # validate-on-publish (T4 spirit): a contract that cannot be checked is
        # rejected here, not discovered as a failed verdict at run time
        try:
            inv_doc = yaml.safe_load(invariants_yaml)
        except Exception as exc:
            raise ValueError(f"invariants.yaml is not valid YAML: {exc}")
        if not isinstance(inv_doc, dict) or not isinstance(inv_doc.get("invariants"), dict):
            raise ValueError(
                "invariants.yaml must be a mapping with a top-level " "'invariants:' section"
            )
        from evarness.core.invariants import check_invariants

        defs = inv_doc["invariants"]
        # dry-run against an empty stream: only MALFORMED contracts reject
        # (an 'eventually' failing on zero events is expected, not invalid)
        bad = [
            r
            for r in check_invariants(list(defs), [], extra=defs)["results"]
            if not r["ok"] and r["detail"].startswith("uncheckable contract")
        ]
        if bad:
            raise ValueError(
                "invalid invariant contract(s): "
                + "; ".join(f"{r['id']}: {r['detail']}" for r in bad)
            )

    graph.id = pattern_id
    graph.metadata = {**graph.metadata, "origin": "studio"}
    d = user_patterns_dir() / pattern_id
    (d / "fixtures").mkdir(parents=True, exist_ok=True)
    (d / "graph.json").write_text(json.dumps(graph.model_dump(by_alias=True), indent=2))
    (d / "lesson.md").write_text(lesson or "")
    for stale in (d / "fixtures").glob("*.yaml"):  # republish replaces the fixture set
        stale.unlink()
    for name, text in fixtures.items():
        (d / "fixtures" / f"{name}.yaml").write_text(text)
    inv_file = d / "invariants.yaml"
    if invariants_yaml.strip():
        inv_file.write_text(invariants_yaml)
    elif inv_file.exists():  # republish without contracts removes them
        inv_file.unlink()
    return _summarize(d, "user")


def delete_pattern(pattern_id: str) -> None:
    """User patterns only — built-ins are content, not data."""
    if pattern_id in builtin_ids():
        raise ValueError("built-in patterns cannot be deleted")
    d = user_patterns_dir() / pattern_id
    if not (d / "graph.json").exists():
        raise ValueError(f"user pattern '{pattern_id}' not found")
    for p in sorted(d.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    d.rmdir()


# ---------------------------------------------------------------- .harness bundles


def export_bundle(pattern_id: str) -> bytes:
    """Zip the pattern directory — the bundle IS the directory shape (DR6)."""
    d = _pattern_dir(pattern_id)
    if not d:
        raise ValueError(f"pattern '{pattern_id}' not found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.rglob("*")):
            if p.is_file():
                z.writestr(f"{pattern_id}/{p.relative_to(d)}", p.read_bytes())
    return buf.getvalue()


def import_bundle(data: bytes, pattern_id: str | None = None) -> dict:
    """Unpack a .harness bundle through the same validation as publish.
    Member paths are sanitized — no absolute paths, no traversal."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        members: dict[tuple, str] = {}
        for info in z.infolist():
            if info.is_dir():
                continue
            parts = Path(info.filename).parts
            if info.filename.startswith("/") or ".." in parts:
                raise ValueError(f"unsafe bundle member path: {info.filename}")
            members[parts] = z.read(info).decode("utf-8")
    roots = {p[0] for p in members if len(p) > 1}
    root = roots.pop() if len(roots) == 1 else None
    strip = 1 if root else 0

    def get(*path):
        return members.get(((root,) if root else ()) + tuple(path))

    graph_text = get("graph.json")
    if graph_text is None:
        raise ValueError("bundle has no graph.json")
    graph_doc = json.loads(graph_text)
    fixtures = {
        p[strip + 1].removesuffix(".yaml"): text
        for p, text in members.items()
        if len(p) == strip + 2 and p[strip] == "fixtures" and p[strip + 1].endswith(".yaml")
    }
    pid = pattern_id or root or graph_doc.get("id", "")
    return publish_pattern(
        str(pid).replace("-", "_"),
        graph_doc,
        get("lesson.md") or "",
        fixtures,
        invariants_yaml=get("invariants.yaml") or "",
    )
