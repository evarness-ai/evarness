"""SQLite persistence + append-only activity log (traceability requirement:
no event, action, or activity goes unnoticed — runs, API calls, CLI commands
all land here and are queryable from the Logs screen)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


def _default_db_path() -> str:
    """Default to a per-user data dir (mounted/network cwds can break SQLite locking).
    Override with EVARNESS_DB."""
    env = os.environ.get("EVARNESS_DB")
    if env:
        return env
    data_dir = Path.home() / ".evarness"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "evarness.db")


_DEFAULT_DB = _default_db_path()

SCHEMA = """
CREATE TABLE IF NOT EXISTS harnesses (
  id TEXT PRIMARY KEY, name TEXT, ir_json TEXT NOT NULL,
  updated_at REAL NOT NULL, origin TEXT DEFAULT 'canvas',
  version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, harness_id TEXT, fixture TEXT, seed INTEGER,
  status TEXT, output TEXT, totals_json TEXT, reason TEXT, created_at REAL,
  experiment_id TEXT, overrides_json TEXT,
  input TEXT, pattern TEXT, approvals_json TEXT, pending_json TEXT,
  fixture_src_json TEXT, invariants_json TEXT
);
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY, harness_id TEXT, name TEXT, fixture TEXT,
  grid_json TEXT NOT NULL, created_at REAL,
  status TEXT DEFAULT 'completed', total_cells INTEGER DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
  run_id TEXT NOT NULL, seq INTEGER NOT NULL, ts REAL, node_id TEXT,
  type TEXT NOT NULL, payload_json TEXT, PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
  actor TEXT NOT NULL, action TEXT NOT NULL, subject TEXT, detail_json TEXT
);
"""


def _conn(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or _DEFAULT_DB
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | None = None) -> None:
    with _conn(db_path) as c:
        c.executescript(SCHEMA)
        # migrate DBs created before harness versioning existed
        cols = [r["name"] for r in c.execute("PRAGMA table_info(harnesses)")]
        if "version" not in cols:
            c.execute("ALTER TABLE harnesses ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        if "status" not in cols:
            c.execute("ALTER TABLE harnesses ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'")
        # migrate DBs created before experiments existed
        run_cols = [r["name"] for r in c.execute("PRAGMA table_info(runs)")]
        if "experiment_id" not in run_cols:
            c.execute("ALTER TABLE runs ADD COLUMN experiment_id TEXT")
            c.execute("ALTER TABLE runs ADD COLUMN overrides_json TEXT")
        # migrate DBs created before pausable approval runs existed
        if "approvals_json" not in run_cols:
            c.execute("ALTER TABLE runs ADD COLUMN input TEXT")
            c.execute("ALTER TABLE runs ADD COLUMN pattern TEXT")
            c.execute("ALTER TABLE runs ADD COLUMN approvals_json TEXT")
            c.execute("ALTER TABLE runs ADD COLUMN pending_json TEXT")
            c.execute("ALTER TABLE runs ADD COLUMN fixture_src_json TEXT")
        if "invariants_json" not in run_cols:  # invariant verdicts
            c.execute("ALTER TABLE runs ADD COLUMN invariants_json TEXT")
        # migrate DBs created before async experiment jobs existed
        exp_cols = [r["name"] for r in c.execute("PRAGMA table_info(experiments)")]
        if "status" not in exp_cols:
            c.execute("ALTER TABLE experiments ADD COLUMN status TEXT DEFAULT 'completed'")
            c.execute("ALTER TABLE experiments ADD COLUMN total_cells INTEGER DEFAULT 0")
            c.execute("ALTER TABLE experiments ADD COLUMN error TEXT")


# ---------------------------------------------------------------- harnesses


def save_harness(
    harness_id: str,
    name: str,
    ir: dict,
    origin: str = "canvas",
    version: int = 1,
    db_path: str | None = None,
) -> None:
    """Upsert. `origin` and `version` are set on insert only — updates keep them."""
    with _conn(db_path) as c:
        c.execute(
            "INSERT INTO harnesses (id, name, ir_json, updated_at, origin, version) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, ir_json=excluded.ir_json, "
            "updated_at=excluded.updated_at",
            (harness_id, name, json.dumps(ir), time.time(), origin, version),
        )


def next_version(name: str, db_path: str | None = None) -> int:
    """Next version number in a lineage; lineage = harnesses sharing a name."""
    with _conn(db_path) as c:
        row = c.execute("SELECT MAX(version) AS v FROM harnesses WHERE name=?", (name,)).fetchone()
    return (row["v"] or 0) + 1


def get_harness(harness_id: str, db_path: str | None = None) -> dict | None:
    with _conn(db_path) as c:
        row = c.execute("SELECT * FROM harnesses WHERE id=?", (harness_id,)).fetchone()
    return dict(row, ir=json.loads(row["ir_json"])) if row else None


def list_harnesses(db_path: str | None = None) -> list[dict]:
    with _conn(db_path) as c:
        rows = c.execute(
            "SELECT id, name, updated_at, origin, version, status FROM harnesses "
            "ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_harness_meta(
    harness_id: str, name: str | None = None, status: str | None = None, db_path: str | None = None
) -> None:
    """Rename and/or set the lifecycle status. A rename also rewrites the name
    INSIDE the stored IR so exports/codegen carry the new name — and because
    version lineage is keyed by name, renaming starts (or joins) that lineage."""
    with _conn(db_path) as c:
        if name is not None:
            row = c.execute("SELECT ir_json FROM harnesses WHERE id=?", (harness_id,)).fetchone()
            if row:
                ir = json.loads(row["ir_json"])
                ir["name"] = name
                c.execute(
                    "UPDATE harnesses SET name=?, ir_json=?, updated_at=? WHERE id=?",
                    (name, json.dumps(ir), time.time(), harness_id),
                )
        if status is not None:
            c.execute(
                "UPDATE harnesses SET status=?, updated_at=? WHERE id=?",
                (status, time.time(), harness_id),
            )


def harness_stats(db_path: str | None = None) -> dict[str, dict]:
    """Per-harness run aggregates, recomputed from the runs table on every read —
    one source of truth: run count, outcome split, avg tokens, est. cost,
    last run time."""
    with _conn(db_path) as c:
        rows = c.execute(
            "SELECT harness_id, status, totals_json, created_at FROM runs "
            "WHERE harness_id IS NOT NULL"
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        s = out.setdefault(
            r["harness_id"],
            {
                "runs": 0,
                "completed": 0,
                "blocked": 0,
                "failed": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "last_run_at": 0.0,
            },
        )
        s["runs"] += 1
        if r["status"] in ("completed", "blocked", "failed"):
            s[r["status"]] += 1
        totals = json.loads(r["totals_json"] or "{}")
        s["tokens"] += int(totals.get("tokens") or 0)
        s["cost_usd"] = round(s["cost_usd"] + float(totals.get("cost_usd") or 0.0), 6)
        s["last_run_at"] = max(s["last_run_at"], r["created_at"] or 0.0)
    for s in out.values():
        s["avg_tokens"] = round(s["tokens"] / s["runs"]) if s["runs"] else 0
    return out


def delete_harness(harness_id: str, db_path: str | None = None) -> None:
    with _conn(db_path) as c:
        c.execute("DELETE FROM harnesses WHERE id=?", (harness_id,))


# ---------------------------------------------------------------- runs & events


def save_run(
    run,
    harness_id: str,
    fixture_name: str,
    seed: int,
    experiment_id: str | None = None,
    overrides: dict | None = None,
    input_text: str | None = None,
    pattern: str | None = None,
    approvals: dict | None = None,
    fixture_src: Any = None,
    db_path: str | None = None,
) -> None:
    """Upsert a run + its events. INSERT OR REPLACE so resuming a paused run
    overwrites the same run_id in place — one run identity that
    transitions paused -> completed/blocked/paused-again."""
    with _conn(db_path) as c:
        c.execute("DELETE FROM run_events WHERE run_id=?", (run.id,))
        c.execute(
            "INSERT OR REPLACE INTO runs (id, harness_id, fixture, seed, status, output, "
            "totals_json, reason, created_at, experiment_id, overrides_json, "
            "input, pattern, approvals_json, pending_json, fixture_src_json, "
            "invariants_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run.id,
                harness_id,
                fixture_name,
                seed,
                run.status,
                json.dumps(run.output),
                json.dumps(run.totals),
                run.reason,
                time.time(),
                experiment_id,
                json.dumps(overrides) if overrides is not None else None,
                input_text,
                pattern,
                json.dumps(approvals or {}),
                json.dumps(run.pending) if run.pending is not None else None,
                json.dumps(fixture_src) if fixture_src is not None else None,
                (
                    json.dumps(run.invariants)
                    if getattr(run, "invariants", None) is not None
                    else None
                ),
            ),
        )
        c.executemany(
            "INSERT INTO run_events VALUES (?,?,?,?,?,?)",
            [
                (run.id, e["seq"], e["ts"], e["node_id"], e["type"], json.dumps(e["payload"]))
                for e in run.events
            ],
        )


def get_run(run_id: str, db_path: str | None = None) -> dict | None:
    with _conn(db_path) as c:
        row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["totals"] = json.loads(d.pop("totals_json") or "{}")
    d["output"] = json.loads(d["output"]) if d["output"] else None
    d["approvals"] = json.loads(d.pop("approvals_json", None) or "{}")
    d["pending"] = json.loads(d["pending_json"]) if d.get("pending_json") else None
    d.pop("pending_json", None)
    d["fixture_src"] = json.loads(d["fixture_src_json"]) if d.get("fixture_src_json") else None
    d.pop("fixture_src_json", None)
    d["invariants"] = json.loads(d["invariants_json"]) if d.get("invariants_json") else None
    d.pop("invariants_json", None)
    return d


def list_runs(harness_id: str | None = None, db_path: str | None = None) -> list[dict]:
    q = "SELECT id, harness_id, fixture, seed, status, reason, created_at FROM runs"
    args: tuple = ()
    if harness_id:
        q += " WHERE harness_id=?"
        args = (harness_id,)
    with _conn(db_path) as c:
        rows = c.execute(q + " ORDER BY created_at DESC LIMIT 200", args).fetchall()
    return [dict(r) for r in rows]


def list_run_events(run_id: str, from_seq: int = 0, db_path: str | None = None) -> list[dict]:
    with _conn(db_path) as c:
        rows = c.execute(
            "SELECT seq, ts, node_id, type, payload_json FROM run_events "
            "WHERE run_id=? AND seq>=? ORDER BY seq",
            (run_id, from_seq),
        ).fetchall()
    return [
        {
            "seq": r["seq"],
            "ts": r["ts"],
            "node_id": r["node_id"],
            "type": r["type"],
            "payload": json.loads(r["payload_json"] or "{}"),
        }
        for r in rows
    ]


# ---------------------------------------------------------------- experiments


def save_experiment(
    exp_id: str,
    harness_id: str,
    name: str,
    fixture: str,
    grid: dict,
    status: str = "completed",
    total_cells: int = 0,
    db_path: str | None = None,
) -> None:
    with _conn(db_path) as c:
        c.execute(
            "INSERT INTO experiments (id, harness_id, name, fixture, grid_json, "
            "created_at, status, total_cells, error) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                exp_id,
                harness_id,
                name,
                fixture,
                json.dumps(grid),
                time.time(),
                status,
                total_cells,
                None,
            ),
        )


def update_experiment_status(
    exp_id: str, status: str, error: str | None = None, db_path: str | None = None
) -> None:
    with _conn(db_path) as c:
        c.execute("UPDATE experiments SET status=?, error=? WHERE id=?", (status, error, exp_id))


def get_experiment(exp_id: str, db_path: str | None = None) -> dict | None:
    with _conn(db_path) as c:
        row = c.execute("SELECT * FROM experiments WHERE id=?", (exp_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["grid"] = json.loads(d.pop("grid_json"))
    return d


def list_experiments(harness_id: str | None = None, db_path: str | None = None) -> list[dict]:
    q = (
        "SELECT e.id, e.harness_id, e.name, e.fixture, e.created_at, e.status, "
        "e.total_cells, COUNT(r.id) AS cells FROM experiments e "
        "LEFT JOIN runs r ON r.experiment_id = e.id"
    )
    args: tuple = ()
    if harness_id:
        q += " WHERE e.harness_id=?"
        args = (harness_id,)
    q += " GROUP BY e.id ORDER BY e.created_at DESC LIMIT 100"
    with _conn(db_path) as c:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def list_experiment_runs(exp_id: str, db_path: str | None = None) -> list[dict]:
    """Cells in sweep order (insertion order = deterministic grid expansion order)."""
    with _conn(db_path) as c:
        rows = c.execute(
            "SELECT * FROM runs WHERE experiment_id=? ORDER BY rowid", (exp_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["totals"] = json.loads(d.pop("totals_json") or "{}")
        d["overrides"] = json.loads(d.pop("overrides_json") or "{}")
        d["invariants"] = json.loads(d["invariants_json"]) if d.get("invariants_json") else None
        d.pop("invariants_json", None)
        out.append(d)
    return out


# ---------------------------------------------------------------- activity log


def log_activity(
    action: str, subject: str = "", actor: str = "api", db_path: str | None = None, **detail
) -> None:
    with _conn(db_path) as c:
        c.execute(
            "INSERT INTO activity_log (ts, actor, action, subject, detail_json) "
            "VALUES (?,?,?,?,?)",
            (time.time(), actor, action, subject, json.dumps(detail)),
        )


def list_activity(
    limit: int = 200, action: str | None = None, db_path: str | None = None
) -> list[dict]:
    q = "SELECT * FROM activity_log"
    args: tuple = ()
    if action:
        q += " WHERE action LIKE ?"
        args = (f"%{action}%",)
    with _conn(db_path) as c:
        rows = c.execute(q + " ORDER BY id DESC LIMIT ?", args + (limit,)).fetchall()
    return [dict(r, detail=json.loads(r["detail_json"] or "{}")) for r in rows]


def new_id(prefix: str = "h") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
