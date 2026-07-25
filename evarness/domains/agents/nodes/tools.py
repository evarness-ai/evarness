"""The tool boundary: manifest safety gate, sim execution with manifest
defaults, and the (ungraduated, refusing) real-execution seam."""

from __future__ import annotations

from __future__ import annotations
from typing import ClassVar, Literal
from pydantic import BaseModel, Field
from evarness.domains.agents.sim import (
    ToolError,
)
from evarness.core.errors import NodeBlocked
from evarness.core.registry import register_node as register

from evarness.domains.agents.nodes.base import (  # noqa: F401
    DEFAULT_AGENT_SYSTEM,
    DEFAULT_LLM_SYSTEM,
    NODE_PRESENTATION,
    REGISTRY,
    NodeSpec,
    _doc_previews,
    _egress_gate,
    _provider_locality,
    _tool_destination,
    as_text,
    presentation,
)


def _tool_spec_gate(tool: str, cfg, node_id: str):
    """T0 safety gate: a tool whose spec says it has side effects (write /
    destructive => approval by default) must be explicitly approved on the node
    (`approve_side_effects`) or the harness refuses the call — safety is
    manifest data, enforced here, not a convention. Returns the spec (or None
    for tools with no manifest, e.g. fixture-only lesson tools)."""
    from evarness.domains.agents.catalog import tool_spec

    spec = tool_spec(tool)
    if spec and spec.safety.approval_required() and not getattr(cfg, "approve_side_effects", False):
        raise NodeBlocked(
            node_id,
            f"tool '{tool}' has {spec.safety.side_effects} "
            "side effects and requires approval — enable "
            "'approve_side_effects' on the node to opt in",
        )
    return spec


def _run_real_tool_contained(tool: str, query: str, cfg, spec, node_id, ctx, sandbox_value: str):
    """Real tool execution has not graduated into this release — every tool
    here runs in simulation. This is a loud governance block, not an import
    error: a graph that asks for `mode: real` gets an actionable refusal, and
    the trace records the block. The `sandbox`/`egress` knobs on the node
    config take effect when real execution arrives (with OS-enforced
    containment); until then they configure nothing and the run refuses before
    reading them."""
    raise NodeBlocked(
        node_id,
        f"tool '{tool}' is set to mode: real, and real tool execution is not "
        "part of this release — every tool here runs in simulation "
        "(fixture-scripted). Set mode: sim, or wait for the release that "
        "introduces sandboxed real execution.",
    )


def _sim_default_result(spec, tool: str, query: str, ctx) -> list[dict]:
    """Sim fallback (T0): when the fixture does not script this tool at all,
    the manifest's default sim rules answer — a fresh install works in sim mode
    immediately. Fixture scripts always win (the lesson author's intent)."""
    if spec is None or not spec.sim or tool in ctx.fixture.tools:
        return []
    from evarness.domains.agents.toolspec import sim_result

    return sim_result(spec, query)


def _warn_free_search(node_id: str, tool: str, mode: str, provider: str, ctx) -> None:
    """A best-effort web.search backend (e.g. free DuckDuckGo) is rate-limited with no
    ranking guarantees — surface that in the trace so the user sees the limitation they
    accepted, not a silent quality drop. Extensible: any provider that sets
    best_effort=True warns, not just a hard-coded name."""
    if mode != "real" or tool != "web.search":
        return
    try:
        from evarness.domains.agents.tools.search import get_search_provider  # type: ignore[import-not-found]
    except ImportError:  # real search backends arrive with real execution
        return

    p = get_search_provider(provider)
    if p is not None and p.best_effort:
        ctx.emit(
            "tool_warning",
            node_id,
            tool=tool,
            provider=p.name,
            warning=f"'{p.name}' is a best-effort search backend: rate-limited, "
            "no ranking guarantees — prefer a configured provider (e.g. "
            "searxng) for reliable results",
        )


@register
class ToolNode(NodeSpec):
    type_name = "tool"
    group = "tools"
    doc = (
        "Tool with a sim twin (fixture-scripted) and real twins behind the same "
        "query->documents contract: web.fetch (read a URL you have; host allowlist, "
        "deny by default), web.search (terms -> ranked sources; searxng or free ddg), "
        "and fs.search (confined under its root). mode: real makes the run "
        "non-deterministic — the trace says so."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "documents"}

    class Config(BaseModel):
        tool: str = "email.search"
        mode: Literal["sim", "real"] = "sim"
        timeout_ms: int = 3000
        # T0 safety opt-in: a tool whose manifest declares write/destructive
        # side effects only runs when the user approves it HERE, per node
        approve_side_effects: bool = False
        retries: int = 2
        allow_hosts: list[str] = Field(
            default_factory=list
        )  # web.fetch allowlist / web.search scope
        root: str = "~/.evarness/sandbox"  # fs.search confinement
        # containment for mode:real. off = in-process (historical); subprocess
        # = child w/ timeout+rlimits+scrubbed env+per-invocation secrets; strict
        # = subprocess + OS confinement (macOS sandbox-exec / Linux bubblewrap:
        # deny network for network:none tools, deny writes outside `root`). Blank
        # = the sandbox.yaml default. A requested-but-unavailable level BLOCKS the run.
        sandbox: Literal["", "off", "subprocess", "strict"] = ""
        # governed egress for a network:outbound tool under sandbox:strict.
        # off = the OS network policy of the tier applies as-is; gateway = the
        # tool reaches ONLY a hostname-allowlisting proxy (allow_hosts), every
        # other destination denied by the OS. Unavailable ⇒ the run BLOCKS.
        egress: Literal["", "off", "gateway"] = ""
        # web.search knobs (ignored by other tools). search_provider names any
        # registered backend; search_options carries provider-specific, non-secret
        # config (e.g. {"searxng_url": "..."}) — API keys come from env, never here.
        search_provider: str = "searxng"  # any registered provider (built-ins: searxng, duckduckgo)
        searxng_url: str = ""  # SearXNG endpoint — user-owned node config (or search_options)
        search_category: str = "general"  # general | news | code | social
        freshness: str = "any"  # any | day | week | month | year
        max_results: int = 8
        search_options: dict[str, str] = Field(default_factory=dict)
        # write-only: holds "" or the secret marker, NEVER the key. The plaintext lives
        # in the vault; a provider reads it via self.secret() at runtime.
        api_key: str = ""

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        query = as_text(inputs.get("in", ""))
        spec = _tool_spec_gate(cfg.tool, cfg, node_id)  # refuses unapproved side effects
        _egress_gate(ctx, node_id, _tool_destination(spec, cfg.mode))  # egress law
        ctx.emit(
            "tool_called",
            node_id,
            tool=cfg.tool,
            query=query[:120],
            mode=cfg.mode,
            **({"tool_version": spec.version} if spec else {}),
        )
        _warn_free_search(node_id, cfg.tool, cfg.mode, getattr(cfg, "search_provider", ""), ctx)
        sim_default = False
        try:
            if cfg.mode == "real":
                result = _run_real_tool_contained(
                    cfg.tool, query, cfg, spec, node_id, ctx, getattr(cfg, "sandbox", "")
                )
            else:
                result = ctx.fixture.tool_result(cfg.tool, query)
                if not result:
                    result = _sim_default_result(spec, cfg.tool, query, ctx)
                    sim_default = bool(result)
        except ToolError as exc:
            ctx.emit("tool_error", node_id, tool=cfg.tool, mode=cfg.mode, error=str(exc))
            return []
        ctx.emit(
            "tool_result",
            node_id,
            tool=cfg.tool,
            count=len(result),
            docs=_doc_previews(result),
            **({"sim_default": True} if sim_default else {}),
        )
        return result
