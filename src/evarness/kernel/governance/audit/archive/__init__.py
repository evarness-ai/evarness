"""Cold-tier audit archive helpers (Phase 5).

This package owns Parquet+zstd writes, SQLite hot-tier compaction,
and DuckDB query/export helpers over hot+cold audit data.
"""

from evarness.kernel.governance.audit.archive.compact import AuditCompactor, CompactResult
from evarness.kernel.governance.audit.archive.markers import VerifyReport, verify_archive
from evarness.kernel.governance.audit.archive.query import AuditQueryEngine, ExportFormat
from evarness.kernel.governance.audit.archive.writer import (
    ArchivePartition,
    AuditArchive,
    default_archive_root,
)

__all__ = [
    "default_archive_root",
    "ArchivePartition",
    "AuditArchive",
    "AuditCompactor",
    "CompactResult",
    "AuditQueryEngine",
    "ExportFormat",
    "VerifyReport",
    "verify_archive",
]
