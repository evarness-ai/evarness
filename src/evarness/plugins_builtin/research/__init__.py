"""Research engine — provider-based web search + crawl + rerank for Evarness.

One tool (``research``) over a pluggable provider chain (SearXNG → Tavily → Brave →
DuckDuckGo), SQLite TTL caching, Trafilatura content extraction, and embedding/lexical
reranking. See ``docs/architecture/research-engine.md``.
"""

from __future__ import annotations

from evarness.plugins_builtin.research.engine import ResearchEngine
from evarness.plugins_builtin.research.models import ResearchInput, ResearchResult, SearchResult
from evarness.plugins_builtin.research.tool import ResearchTool, run_research

__all__ = [
    "ResearchEngine",
    "ResearchInput",
    "ResearchResult",
    "ResearchTool",
    "SearchResult",
    "run_research",
]
