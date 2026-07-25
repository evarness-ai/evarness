"""Test isolation: the suite must never touch the developer's real
``~/.evarness`` — the activity-log DB goes to a per-session temp dir, and the
user-overlay env vars are cleared so a developer's local overlays can't change
test outcomes. Set BEFORE any evarness import: ``evarness.core.store`` resolves its
DB path (and creates the data dir) at import time."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="evarness-tests-")
os.environ["EVARNESS_DB"] = os.path.join(_tmp, "evarness.db")
for var in (
    "EVARNESS_PROMPTS",
    "EVARNESS_TOOLS",
    "EVARNESS_TIERS",
    "EVARNESS_PROVIDERS",
    "EVARNESS_INVARIANTS",
    "EVARNESS_GROUNDING",
    "EVARNESS_CLASSIFICATION",
    "EVARNESS_EXPORTERS",
    "EVARNESS_JUDGES",
):
    os.environ.pop(var, None)
