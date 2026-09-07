"""SQLite schema init/connection -- the only file besides
sqlite_interaction_repository.py that knows SQLite exists. A future
migration (e.g. to a private server's Postgres) replaces this module and
sqlite_interaction_repository.py only; InteractionRepository and every
caller of it stay unchanged."""
from __future__ import annotations

import sqlite3
from pathlib import Path

CURRENT_SCHEMA_VERSION = 1

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
        closed_at TEXT
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


def initialize_schema(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
        (str(CURRENT_SCHEMA_VERSION),),
    )
    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row is not None else 0
