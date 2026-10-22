"""System agent — host status + Evarness self-introspection (S0).

The 7th PAIA agent. Most of "system" already exists (chat, time/date, weather,
identity via the system ReAct handler; host pressure is sampled internally for
the LLM tier governor). The net-new piece is a **user-facing status surface**
that composes signals Evarness already has: host resources + what Evarness itself has
connected and running. Deterministic — pure reads, no LLM, no OS mutation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from evarness.foundation.process_state import track_globals


@dataclass(frozen=True)
class HostStatus:
    ram_free_gb: float
    ram_total_gb: float
    cpu_percent: float
    thermal_throttled: bool
    cpu_speed_limit: int  # 100 = unthrottled

    def summary(self) -> str:
        thermal = " (thermal-throttled)" if self.thermal_throttled else ""
        return (
            f"RAM {self.ram_free_gb:.1f}/{self.ram_total_gb:.1f} GB free, "
            f"CPU {self.cpu_percent:.0f}%{thermal}"
        )


@dataclass(frozen=True)
class EvarnessStatus:
    accounts: dict[str, int]  # provider → connected-account count
    skill_count: int
    heartbeat_count: int  # enabled heartbeats
    database_sizes: dict[str, int]  # db filename → bytes
    filemanager_roots: int
    audit_writes: dict[str, object] = field(default_factory=dict)  # audit.write_health


@dataclass(frozen=True)
class SystemReport:
    host: HostStatus
    evarness: EvarnessStatus = field(default=None)  # type: ignore[assignment]


logger = logging.getLogger(__name__)


#: How many file roots the user has allowed. The file domain leaves the core at M6
#: (OSS plan M6, decision 2), so it registers this; a core-only install reports 0.
_root_counter: Callable[[], int] | None = None


def register_file_root_counter(counter: Callable[[], int] | None) -> None:
    """Register the allowed-file-root count (``None`` clears it)."""
    global _root_counter
    _root_counter = counter


def _count_file_roots(root_registry: object | None) -> int:
    """An injected registry wins; then the registered counter; then nothing mounted."""
    if root_registry is not None:
        return len(root_registry.list_roots())  # type: ignore[attr-defined]
    if _root_counter is None:
        return 0
    try:
        return _root_counter()
    except Exception:  # a status read never fails on one contributor
        logger.exception("status: the file-root counter failed")
        return 0


#: Connected accounts by provider. The account store belongs to the domains (core/SDK
#: boundary plan, PR 2), so the plugin that owns it registers this; a core-only install
#: reports none. Same shape as the file-root counter above.
_account_counter: Callable[[], dict[str, int]] | None = None


def register_account_counter(counter: Callable[[], dict[str, int]] | None) -> None:
    """Register the connected-account count by provider (``None`` clears it)."""
    global _account_counter
    _account_counter = counter


def connected_accounts() -> dict[str, int] | None:
    """Connected accounts by provider from the registered counter, or ``None`` when no
    plugin registered one (a core-only install, or a process that mounted no domain):
    what ``evarness doctor`` reports as its Accounts row."""
    if _account_counter is None:
        return None
    return _count_accounts(None)


def _count_accounts(accounts_store: object | None) -> dict[str, int]:
    """An injected store wins; then the registered counter; then nothing mounted."""
    if accounts_store is not None:
        accounts: dict[str, int] = {}
        accounts_store.ensure_schema()  # type: ignore[attr-defined]
        for acc in accounts_store.list(active_only=False):  # type: ignore[attr-defined]
            accounts[acc.provider] = accounts.get(acc.provider, 0) + 1
        return accounts
    if _account_counter is None:
        return {}
    try:
        return dict(_account_counter())
    except Exception:  # a status read never fails on one contributor
        logger.exception("status: the account counter failed")
        return {}


def host_status() -> HostStatus:
    """Snapshot host resources (reuses the tier governor's pressure sampler)."""
    import psutil

    from evarness.foundation.observability.host_pressure import sample_pressure

    snap = sample_pressure()
    total_gb = psutil.virtual_memory().total / (1024**3)
    return HostStatus(
        ram_free_gb=snap.ram_free_gb,
        ram_total_gb=total_gb,
        cpu_percent=snap.cpu_percent,
        thermal_throttled=snap.thermal_throttled,
        cpu_speed_limit=snap.cpu_speed_limit,
    )


def evarness_status(
    *,
    accounts_store: object | None = None,
    skills_dir: Path | None = None,
    heartbeats_path: Path | None = None,
    data_dir: Path | None = None,
    root_registry: object | None = None,
) -> EvarnessStatus:
    """Introspect Evarness itself: connections, skills, heartbeats, DBs, FM roots."""
    from evarness.foundation.paths import config_path
    from evarness.foundation.paths import data_dir as resolve_data_dir
    from evarness.services.heartbeat.config import load_heartbeats

    data_dir = data_dir or resolve_data_dir()

    skills_dir = skills_dir or config_path("skills")
    heartbeats_path = heartbeats_path or config_path("heartbeats.yaml")
    accounts = _count_accounts(accounts_store)

    skill_count = sum(1 for _ in skills_dir.rglob("manifest.yaml")) if skills_dir.exists() else 0

    heartbeats = load_heartbeats(heartbeats_path) if heartbeats_path.exists() else []
    heartbeat_count = sum(1 for h in heartbeats if h.enabled)

    database_sizes = (
        {p.name: p.stat().st_size for p in sorted(data_dir.glob("*.db"))}
        if data_dir.exists()
        else {}
    )

    filemanager_roots = _count_file_roots(root_registry)

    from evarness.foundation.paths import audit_db_path
    from evarness.kernel.governance.audit.write_health import write_health

    return EvarnessStatus(
        accounts=accounts,
        skill_count=skill_count,
        heartbeat_count=heartbeat_count,
        database_sizes=database_sizes,
        filemanager_roots=filemanager_roots,
        audit_writes=write_health(audit_db_path()),
    )


def system_report() -> SystemReport:
    """Combined host + Evarness status."""
    return SystemReport(host=host_status(), evarness=evarness_status())


__all__ = [
    "HostStatus",
    "EvarnessStatus",
    "SystemReport",
    "host_status",
    "evarness_status",
    "system_report",
]

# Process-wide state: put back when a harness run ends (foundation/process_state.py).
track_globals(__name__, "_root_counter", "_account_counter")
