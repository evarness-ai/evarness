"""The published documentation site, for messages that send the owner to a page.

An installed ``evarness`` (uv tool, pipx) has no ``docs/`` tree beside it, so a message
that names ``docs/getting-started/install.md`` sends a tester nowhere. Every CLI hint
that points at a page builds its address here. ``evarness_personal`` keeps its own copy
(``evarness_personal.email.docs_site``) because that tree imports only the SDK.
"""

from __future__ import annotations

DOCS_SITE = "https://evarness-ai.github.io/evarness/"


def docs_url(page: str) -> str:
    """The published address of a page under ``docs/``, given as ``getting-started/install``
    (no ``docs/`` prefix, no ``.md``); an ``#anchor`` is kept."""
    path, _, anchor = page.partition("#")
    path = path.strip("/")
    if path.endswith(".md"):
        path = path[:-3]
    if path == "index" or path.endswith("/index"):
        path = path[: -len("index")].rstrip("/")
    url = DOCS_SITE + (path + "/" if path else "")
    return f"{url}#{anchor}" if anchor else url


__all__ = ["DOCS_SITE", "docs_url"]
