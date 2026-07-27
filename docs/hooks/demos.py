"""MkDocs hook: the live demos are built BY the product, and checked.

Before every site build this generates the demo artifacts by driving the real
CLI — a replay of the flagship happy path, the blocked run, and a proof
browser over a freshly proven bundle — then asserts that the digests embedded
in those artifacts are byte-for-byte the reference digests the docs cite
(README, E2E.md, the golden-digest tests). If a digest does not reproduce,
the DOCS BUILD FAILS: this site cannot publish a claim the build didn't just
demonstrate. The generated files land in docs/demos/ (gitignored — they are
build products, not sources).
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent
REPO = DOCS.parent
PATTERN_DIR = REPO / "evarness" / "domains" / "agents" / "patterns" / "governed_email_assistant"

# the reference digests this site's prose cites — the same values pinned by
# tests/test_golden_digests.py; a mismatch here is a c1 contract event, never
# a paste-the-new-value moment
EXPECTED = {
    "replay.html": "c1:sha256:e0513e746ea1d229cc33ed576624882efb1eef7124830022dd2e6cdaeca98086",
    "blocked.html": "c1:sha256:66d4021cf235f88c7b49cf3fa6c8c7b5d0c01ca93844c2e8c7cebcf0f58df658",
}

_ISLAND = re.compile(r'<script type="application/json" id="evarness-data">(.*?)</script>', re.S)


def _island(path: Path) -> dict:
    match = _ISLAND.search(path.read_text())
    if not match:
        raise SystemExit(f"docs demos: no data island in {path.name}")
    return json.loads(match.group(1))


def on_pre_build(config) -> None:
    from evarness.cli import main

    demos = DOCS / "demos"
    demos.mkdir(exist_ok=True)
    graph = str(PATTERN_DIR / "graph.json")

    with tempfile.TemporaryDirectory() as tmp:
        proof = str(Path(tmp) / "proof.json")
        jobs = [
            (
                [
                    "run",
                    graph,
                    "--fixture",
                    str(PATTERN_DIR / "fixtures" / "happy.yaml"),
                    "--html",
                    str(demos / "replay.html"),
                ],
                {0},
            ),
            (
                [
                    "run",
                    graph,
                    "--fixture",
                    str(PATTERN_DIR / "fixtures" / "failure.yaml"),
                    "--html",
                    str(demos / "blocked.html"),
                ],
                {1},
            ),  # blocked = exit 1, by design
            (["prove", "governed_email_assistant", "-o", proof], {0}),
        ]
        for argv, ok_codes in jobs:
            code = main(argv)
            if code not in ok_codes:
                raise SystemExit(f"docs demos: `evarness {argv[0]}` exited {code}")
        code = main(["render", proof, "-o", str(demos / "proof-browser.html")])
        if code != 0:
            raise SystemExit(f"docs demos: `evarness render` exited {code}")
        # the proof browser embeds the whole bundle; also publish the raw
        # bundle next to it so "extract and verify" has a download too
        shutil.copy(proof, demos / "proof.json")

    for name, want in EXPECTED.items():
        got = _island(demos / name)["meta"].get("trace_digest")
        if got != want:
            raise SystemExit(
                f"docs demos: {name} digest drifted.\n  expected {want}\n  got      {got}\n"
                "The docs cite this digest as reproducible; a change here is a "
                "c1 contract event (see DECISIONS.md E3), not a docs problem."
            )
    browser = _island(demos / "proof-browser.html")
    verdict = (browser["bundle"].get("verdict") or {}).get("ok")
    if verdict is not True:
        raise SystemExit(f"docs demos: proof bundle verdict is {verdict!r}, expected True")


# The site includes the canonical markdown verbatim — no forked prose. The
# hook performs the inclusion itself (a `--8<-- "FILE"` line pulls the file
# from the repo root) so that repo-relative links can be rewritten to their
# site-context targets BEFORE MkDocs validates them; content is untouched.
_INCLUDE = re.compile(r'^--8<-- "([^"]+)"$', re.M)

_LINK_MAP = {
    "docs/GUIDE.md": "GUIDE.md",
    "docs/E2E.md": "E2E.md",
    "docs/tutorial-custom-node.md": "tutorial-custom-node.md",
    "docs/tutorial-domain-plugin.md": "tutorial-domain-plugin.md",
    "docs/ONBOARDING.md": "ONBOARDING.md",
    "docs/PROVE-VERIFY.md": "PROVE-VERIFY.md",
    "ARCHITECTURE.md": "architecture.md",
    "DECISIONS.md": "https://github.com/evarness-ai/evarness/blob/main/DECISIONS.md",
    "SECURITY.md": "security.md",
    "CONTRIBUTING.md": "contributing.md",
    "LICENSE": "https://github.com/evarness-ai/evarness/blob/main/LICENSE",
}


def _site_links(text: str) -> str:
    for src, dst in _LINK_MAP.items():
        text = text.replace(f"]({src})", f"]({dst})")
    return text


def on_page_markdown(markdown: str, page, config, files) -> str:
    def include(match: re.Match) -> str:
        target = REPO / match.group(1)
        if not target.is_file():
            raise SystemExit(f"docs include: {match.group(1)} not found at repo root")
        return _site_links(target.read_text())

    return _site_links(_INCLUDE.sub(include, markdown))
