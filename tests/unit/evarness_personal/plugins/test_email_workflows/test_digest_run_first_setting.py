"""The email plugin declares the setting the morning digest's ``run_first`` reads.

``config/digest.yaml`` runs the ``email_sweep`` and ``email_judge`` jobs before the
digest only while ``EVARNESS_EMAIL_JUDGE`` is on, read with its declared default. The core's
test of that ordering (tests/unit/evarness/runtime/test_bootstrap/
test_digest_run_first.py) uses a manifest that declares it on by default; this pins that
the real plugin does too (moved here from that test's registry, which read the real
manifest).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from evarness.runtime.plugin_host.loader import discover_plugin
from evarness.runtime.settings_catalog import bool_setting, registry_catalog

PROJECT_ROOT = Path(__file__).resolve().parents[5]


class _Registry:
    def plugins(self) -> list[object]:
        source = discover_plugin("email_workflows")
        assert source is not None
        return [source]


def test_the_email_plugin_declares_the_judge_on_by_default() -> None:
    source = discover_plugin("email_workflows")
    assert source is not None
    declared = source.manifest.settings["EVARNESS_EMAIL_JUDGE"]
    assert declared.kind == "bool" and declared.default is True
    assert bool_setting("EVARNESS_EMAIL_JUDGE", registry_catalog(_Registry())) is True


def test_the_shipped_digest_runs_the_email_jobs_first_on_that_setting() -> None:
    digest = yaml.safe_load((PROJECT_ROOT / "config" / "digest.yaml").read_text("utf-8"))
    run_first = {job["heartbeat"]: job.get("if_setting") for job in digest["run_first"]}
    assert run_first == {
        "email_sweep": "EVARNESS_EMAIL_JUDGE",
        "email_judge": "EVARNESS_EMAIL_JUDGE",
    }
