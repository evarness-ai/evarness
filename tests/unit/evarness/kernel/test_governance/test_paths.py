"""Governance data/config path resolution + test isolation (no Path.home() leak)."""

from __future__ import annotations

from pathlib import Path

from evarness.foundation.paths import governance_config_dir, governance_data_dir


def test_evarness_home_relocates_data_and_config(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EVARNESS_HOME", "/tmp/evarness-home-abc")
    assert governance_data_dir() == Path("/tmp/evarness-home-abc/governance")
    assert governance_config_dir() == Path("/tmp/evarness-home-abc/governance")


def test_production_paths_when_evarness_home_unset(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("EVARNESS_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # a home with no 0.1.x directories to fall back to
    assert governance_data_dir() == Path.home() / ".local" / "share" / "evarness"
    assert governance_config_dir() == Path.home() / ".config" / "evarness"


def test_store_defaults_resolved_under_test_home() -> None:
    # conftest sets EVARNESS_HOME to a throwaway temp dir before any import, so each
    # store's default points under it -- never the real profile.
    import os

    from evarness.kernel.governance.approvals.store import default_approvals_db_path
    from evarness.kernel.governance.audit.log import AuditLog
    from evarness.kernel.governance.cost.store import default_cost_ledger_db_path
    from evarness.kernel.governance.side_effects.store import default_ledger_db_path
    from evarness.memory.state.store import default_checkpoint_db_path

    home = os.environ["EVARNESS_HOME"]  # set by conftest
    gov = Path(home) / "governance"
    assert default_checkpoint_db_path() == gov / "checkpoints.db"
    assert default_cost_ledger_db_path() == gov / "cost-ledger.db"
    assert default_approvals_db_path() == gov / "approvals.db"
    assert default_ledger_db_path() == gov / "side_effects.db"
    assert AuditLog().db_path == gov / "audit.db"


def test_store_defaults_follow_a_home_moved_after_import(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Resolved on every use, never frozen at import: a process that relocates EVARNESS_HOME
    # after importing Evarness (evarness.testing's harness) writes into the new home.
    from evarness.foundation.observability.session_log import session_log_dir
    from evarness.kernel.governance.approvals.store import default_approvals_db_path
    from evarness.kernel.governance.audit.log import AuditLog
    from evarness.memory.identity.loader import user_md_path
    from evarness.memory.state.store import default_checkpoint_db_path

    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path))
    monkeypatch.delenv("EVARNESS_GOVERNANCE_AUDIT_DB_PATH", raising=False)
    monkeypatch.delenv("EVARNESS_SESSION_LOG_DIR", raising=False)
    gov = tmp_path / "governance"
    assert default_approvals_db_path() == gov / "approvals.db"
    assert default_checkpoint_db_path() == gov / "checkpoints.db"
    assert AuditLog().db_path == gov / "audit.db"
    assert user_md_path() == tmp_path / "workspace" / "USER.md"
    assert session_log_dir() == tmp_path / "logs"
