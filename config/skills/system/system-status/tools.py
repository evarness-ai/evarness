"""LangChain tool for the system-status skill (System agent S0).

Thin wrapper over ``evarness.services.system`` — a deterministic snapshot of host resources
and Evarness's own state (connections, skills, heartbeats, DBs). Read-only; domain
logic + tests live in src/evarness/system.
"""

from __future__ import annotations

from evarness.services.system.status import host_status, evarness_status
from langchain_core.tools import BaseTool
from pydantic import BaseModel


class _NoArgs(BaseModel):
    pass


class SystemStatusTool(BaseTool):
    name: str = "system_status"
    description: str = (
        "Report Evarness's system status: host resources (RAM/CPU/thermal) and "
        "what Evarness has connected and running (accounts, skills, heartbeats, "
        "databases, FileManager roots)."
    )
    args_schema: type[BaseModel] = _NoArgs

    def _run(self) -> dict[str, object]:
        host = host_status()
        evarness = evarness_status()
        return {
            "ram_free_gb": round(host.ram_free_gb, 1),
            "ram_total_gb": round(host.ram_total_gb, 1),
            "cpu_percent": round(host.cpu_percent, 0),
            "thermal_throttled": host.thermal_throttled,
            "accounts": evarness.accounts,
            "skills": evarness.skill_count,
            "heartbeats": evarness.heartbeat_count,
            "filemanager_roots": evarness.filemanager_roots,
            "databases": dict(evarness.database_sizes),
        }


SKILL_TOOLS = [SystemStatusTool]
