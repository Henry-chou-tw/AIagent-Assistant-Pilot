"""SQLite schema init/connection -- the only file besides
sqlite_interaction_repository.py that knows SQLite exists. A future
migration (e.g. to a private server's Postgres) replaces this module and
sqlite_interaction_repository.py only; InteractionRepository and every
caller of it stay unchanged."""
from __future__ import annotations

import sqlite3
from pathlib import Path

CURRENT_SCHEMA_VERSION = 2

_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS interactions (
        id TEXT PRIMARY KEY,
        schema_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        source TEXT NOT NULL,
        channel_ref TEXT,
        raw_input TEXT NOT NULL,
        action_type TEXT NOT NULL,
        domain TEXT,
        due_at TEXT,
        agent_response TEXT,
        model_used TEXT,
        input_tokens INTEGER,
        output_tokens INTEGER,
        latency_ms REAL,
        estimated_cost_usd REAL,
        purpose TEXT,
        tool_executions_json TEXT NOT NULL,
        henry_correction TEXT,
        final_action_type TEXT,
        final_domain TEXT,
        task_status TEXT NOT NULL,
        related_interaction_id TEXT,
        closure_outcome TEXT,
        closed_at TEXT,
        next_check_at TEXT,
        last_checked_at TEXT,
        reminder_count INTEGER NOT NULL DEFAULT 0,
        waiting_on TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_interactions_action_type ON interactions(action_type)",
    "CREATE INDEX IF NOT EXISTS idx_interactions_task_status ON interactions(task_status)",
    "CREATE INDEX IF NOT EXISTS idx_interactions_created_at ON interactions(created_at)",
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
]


def connect(database_path: str) -> sqlite3.Connection:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_due_at_column(conn: sqlite3.Connection) -> None:
    """Additive migration for pre-existing databases created before
    2026-09-07 (when due_at didn't exist yet). CREATE TABLE IF NOT EXISTS
    doesn't retrofit columns onto an already-existing table, so this
    checks PRAGMA table_info and ALTER TABLE ADD COLUMN only if missing --
    never touches existing rows/columns."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(interactions)")}
    if "due_at" not in cols:
        conn.execute("ALTER TABLE interactions ADD COLUMN due_at TEXT")


def _ensure_followup_columns(conn: sqlite3.Connection) -> None:
    """Additive migration (2026-09-07, Follow-up Runtime Closure round) for
    databases created before next_check_at/last_checked_at/reminder_count/
    waiting_on existed. Same discipline as _ensure_due_at_column: only ADD
    COLUMN when missing, never touches existing rows. reminder_count gets
    DEFAULT 0 so every pre-existing row reads back as 0, not NULL (it's a
    counter, not an optional fact); the other three stay NULL, which is
    the honest "we don't know yet / not applicable" value for old rows
    that were never classified against this new axis."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(interactions)")}
    if "next_check_at" not in cols:
        conn.execute("ALTER TABLE interactions ADD COLUMN next_check_at TEXT")
    if "last_checked_at" not in cols:
        conn.execute("ALTER TABLE interactions ADD COLUMN last_checked_at TEXT")
    if "reminder_count" not in cols:
        conn.execute("ALTER TABLE interactions ADD COLUMN reminder_count INTEGER NOT NULL DEFAULT 0")
    if "waiting_on" not in cols:
        conn.execute("ALTER TABLE interactions ADD COLUMN waiting_on TEXT")


def initialize_schema(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        conn.execute(statement)
    _ensure_due_at_column(conn)
    _ensure_followup_columns(conn)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
        (str(CURRENT_SCHEMA_VERSION),),
    )
    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row is not None else 0
