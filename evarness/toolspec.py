"""ToolSpec — the ONE definition of what a tool is (T0 of the Tools & Skills SDK).

Before this module a "tool" was five disconnected representations (a catalog
dict, a REAL_TOOLS function, node Config fields, frontend metadata, a fixture
sim twin) that agreed only by convention. Now a tool is a validated pydantic
manifest — YAML/JSON on disk — and everything else derives from it:

- **identity ≠ execution**: ``id`` names the tool; ``executor`` says HOW it runs
  (a binding). v1 bindings: ``builtin`` (ships with the engine), ``sim`` (no real
  twin — fixture/spec-scripted only). Reserved and validated now, implemented in
  later phases: ``http`` (declarative endpoint spec — the submit-safe universal
  format), ``mcp`` (import an MCP server's tools), ``python`` (local plugins dir,
  pip entry points later).
- **contract**: every binding returns ``query -> list[documents]``
  ({id, subject, snippet, source}) — mandatory, it is what makes tools
  interchangeable, retrievable, and grounding-checkable.
- **safety is data**: ``side_effects: read|write|destructive`` + ``network``;
  anything beyond read requires approval BY DEFAULT (override with an explicit
  ``requires_approval: false``). The node must opt in (``approve_side_effects``)
  or the harness refuses the call — governance, not convention.
- **sim twin ships with the tool**: ``sim`` rules make a freshly installed tool
  work in sim mode immediately; fixture scripts still override (the lesson
  author's intent wins).
- **update semantics**: a tool update NEVER silently changes an existing
  harness — the user updates and re-runs deliberately; every ``tool_called``
  event records ``tool_version`` so traces are honest about what ran.
- **future-proof fields reserved now** (T4 marketplace): ``trust_level``,
  ``validated``, ``signature`` — process gets added later, the format doesn't
  change.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")

#: category groups the UI and carries governance defaults for its members
CATEGORIES = ("web", "email", "files", "calendar", "comms", "data", "code", "knowledge", "system")

EXECUTOR_KINDS = ("builtin", "sim", "http", "mcp", "python")
IMPLEMENTED_KINDS = {"builtin", "sim", "http", "python", "mcp"}


class ToolArg(BaseModel):
    """One declared parameter — typed and validated."""

    key: str
    label: str = ""
    type: Literal["text", "number", "bool", "list", "enum"] = "text"
    required: bool = False
    default: Any = None
    options: list[str] = Field(default_factory=list)  # for enum
    pattern: str | None = None  # regex constraint (text)
    min: float | None = None
    max: float | None = None
    help: str = ""
    examples: list[str] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _key_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]{0,63}$", v):
            raise ValueError(f"arg key '{v}' must be snake_case")
        return v

    @model_validator(mode="after")
    def _consistent(self):
        if self.type == "enum" and not self.options:
            raise ValueError(f"enum arg '{self.key}' needs non-empty options")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"arg '{self.key}' pattern is not valid regex: {exc}")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"arg '{self.key}': min > max")
        if not self.label:
            self.label = self.key.replace("_", " ").capitalize()
        return self


class SafetySpec(BaseModel):
    """Safety is manifest data, not convention. Approval defaults from the
    side-effect class: anything that WRITES needs a human opt-in unless the
    author explicitly says otherwise (user decision, 2026-07-12)."""

    side_effects: Literal["read", "write", "destructive"] = "read"
    network: Literal["none", "outbound"] = "none"
    requires_approval: bool | None = None  # None => derived from side_effects

    def approval_required(self) -> bool:
        if self.requires_approval is not None:
            return self.requires_approval
        return self.side_effects != "read"


class HttpResultMap(BaseModel):
    """How a JSON response becomes the mandatory documents contract. Paths are
    dot-paths ("data.results", "0.title"); items "" means the response IS the list."""

    items: str = ""
    id: str = "id"
    subject: str = "subject"
    snippet: str = "snippet"


class HttpSpec(BaseModel):
    """The declarative http binding (T1) — the submit-safe universal format:
    wrap any JSON API in a manifest, zero code. Templates may use ``{query}``,
    any declared arg key (``{count}``), and ``{secret:name}`` (vault-resolved
    at call time, never stored)."""

    url: str
    method: Literal["GET", "POST"] = "GET"
    query_params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict | None = None  # JSON body template (POST)
    result: HttpResultMap = Field(default_factory=HttpResultMap)
    timeout_ms: int = 6000
    # http (not https) is refused unless the author opts in — localhost is fine
    allow_insecure: bool = False

    @field_validator("url")
    @classmethod
    def _scheme(cls, v: str) -> str:
        if not re.match(r"^https?://", v):
            raise ValueError("http binding url must start with http(s)://")
        return v


class McpSpec(BaseModel):
    """The mcp binding (T2): the tool lives on an MCP server; ``executor.ref``
    names the server-side tool. One of ``command`` (stdio) or ``url``
    (streamable http) locates the server. The engine connects per call —
    stateless and simple; session pooling can come later if latency matters."""

    command: str = ""  # stdio: e.g. "python -m acme_mcp"
    url: str = ""  # streamable http endpoint
    query_arg: str = "query"  # which server-side argument gets the query
    args: dict[str, str] = Field(default_factory=dict)  # extra args (templated)

    @model_validator(mode="after")
    def _target(self):
        if bool(self.command) == bool(self.url):
            raise ValueError("mcp spec needs exactly one of command (stdio) " "or url (http)")
        return self


class ExecutorSpec(BaseModel):
    """HOW the tool runs — the binding. ``ref`` points at the concrete thing:
    a builtin executor name, a registered python plugin function, an MCP
    server-side tool name; ``http`` carries the declarative endpoint spec."""

    kind: Literal["builtin", "sim", "http", "mcp", "python"] = "sim"
    ref: str = ""
    http: HttpSpec | None = None
    mcp: McpSpec | None = None

    @model_validator(mode="after")
    def _ref_needed(self):
        if self.kind == "builtin" and not self.ref:
            raise ValueError("executor kind 'builtin' needs ref (the executor name)")
        if self.kind == "python" and not self.ref:
            raise ValueError(
                "executor kind 'python' needs ref (the registered "
                "plugin tool name — see register_tool)"
            )
        if self.kind == "http" and self.http is None:
            raise ValueError(
                "executor kind 'http' needs an http: spec " "(url, method, result mapping)"
            )
        if self.kind == "mcp":
            if not self.ref:
                raise ValueError("executor kind 'mcp' needs ref (the server-side " "tool name)")
            if self.mcp is None:
                raise ValueError("executor kind 'mcp' needs an mcp: spec " "(command or url)")
        return self


class SimRule(BaseModel):
    """Default sim behavior shipped WITH the tool: a fresh install works in sim
    mode immediately. Empty match = catch-all. '{query}' in result strings is
    substituted with the actual query."""

    match: dict[str, str] = Field(default_factory=dict)
    result: list[dict] = Field(default_factory=list)


class TestCase(BaseModel):
    """Declared behavioral test — run at publish time (T4) and by users."""

    input: dict = Field(default_factory=dict)
    expect: dict = Field(default_factory=dict)


class ToolSpec(BaseModel):
    id: str
    name: str = ""
    version: str = "0.1.0"
    category: Literal[
        "web", "email", "files", "calendar", "comms", "data", "code", "knowledge", "system"
    ] = "data"
    description: str
    args: list[ToolArg] = Field(default_factory=list)
    safety: SafetySpec = Field(default_factory=SafetySpec)
    executor: ExecutorSpec = Field(default_factory=ExecutorSpec)
    secrets: list[str] = Field(default_factory=list)  # vault-resolved
    tests: list[TestCase] = Field(default_factory=list)
    sim: list[SimRule] = Field(default_factory=list)
    source: Literal["builtin", "user"] = "user"
    # reserved for the T4 verification pipeline / marketplace — format-stable now
    trust_level: Literal["first-party", "community"] = "community"
    validated: dict = Field(default_factory=dict)  # {status, date, notes}
    signature: str = ""

    @field_validator("id")
    @classmethod
    def _id_slug(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError("id must be a slug: lowercase letters, digits, . _ - (2-64 chars)")
        return v

    @field_validator("description")
    @classmethod
    def _desc(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a description is required")
        return v

    @field_validator("version")
    @classmethod
    def _semverish(cls, v: str) -> str:
        if not re.match(r"^\d+\.\d+(\.\d+)?$", v):
            raise ValueError("version must look like 1.2 or 1.2.3")
        return v

    @model_validator(mode="after")
    def _fill(self):
        if not self.name:
            self.name = self.id
        # http/mcp tools talk beyond the process by definition — derived, not
        # trusted (found live: a manifest declared network:none around an http call)
        if self.executor.kind in ("http", "mcp") and self.safety.network == "none":
            self.safety.network = "outbound"
        return self


# ---------------------------------------------------------------- builtins

_PACKAGED = Path(__file__).parent / "tools.yaml"
_builtin_cache: list[ToolSpec] | None = None


def builtin_specs() -> list[ToolSpec]:
    """The engine's own tools, loaded from the packaged manifest — the format
    is dogfooded, not special-cased."""
    global _builtin_cache
    if _builtin_cache is None:
        doc = yaml.safe_load(_PACKAGED.read_text()) or {}
        _builtin_cache = [
            ToolSpec.model_validate({**t, "source": "builtin", "trust_level": "first-party"})
            for t in doc.get("tools", [])
        ]
    return _builtin_cache


def from_legacy(doc: dict) -> ToolSpec:
    """Map the pre-T0 catalog JSON shape (flat executor string, params list)
    into a ToolSpec, so previously published user tools keep loading."""
    if "executor" in doc and not isinstance(doc.get("executor"), dict):
        execu = doc.pop("executor", None)
        doc["executor"] = {"kind": "builtin", "ref": execu} if execu else {"kind": "sim"}
    if "params" in doc and "args" not in doc:
        doc["args"] = [
            {
                "key": p.get("key", ""),
                "label": p.get("label", ""),
                "type": (
                    p.get("type", "text")
                    if p.get("type") in ("text", "number", "bool", "list", "enum")
                    else "text"
                ),
                "help": p.get("help", ""),
            }
            for p in doc.pop("params", [])
        ]
    doc.pop("note", None)
    return ToolSpec.model_validate(doc)


def sim_result(spec: ToolSpec, query: str) -> list[dict]:
    """Evaluate the spec's default sim rules — same matching semantics as
    fixtures (query_contains), plus catch-all rules and '{query}' substitution."""
    low = query.lower()
    for rule in spec.sim:
        needle = str(rule.match.get("query_contains", "")).lower()
        if needle and needle not in low:
            continue
        return [
            {k: (v.replace("{query}", query) if isinstance(v, str) else v) for k, v in doc.items()}
            for doc in rule.result
        ]
    return []


__all__ = [
    "ToolSpec",
    "ToolArg",
    "SafetySpec",
    "ExecutorSpec",
    "HttpSpec",
    "McpSpec",
    "HttpResultMap",
    "SimRule",
    "TestCase",
    "CATEGORIES",
    "EXECUTOR_KINDS",
    "IMPLEMENTED_KINDS",
    "builtin_specs",
    "from_legacy",
    "sim_result",
]
