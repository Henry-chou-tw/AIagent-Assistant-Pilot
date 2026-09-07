"""SQLite implementation of InteractionRepository (Pilot Step 5). SQLite
is an implementation choice, not an architecture commitment -- nothing
outside this file and db.py knows SQLite exists; a future migration to a
private server's database only touches these two files."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Iterable, Optional

from .. import schema_validation
from ..models import Interaction, ToolExecution
from .interaction_repository import InteractionRepository


def _parse_dt(value: Optional[str]) -> Optional[dt.datetime]:
    if value is None:
        return None
    return dt.datetime.fromisoformat(value)


class SqliteInteractionRepository(InteractionRepository):
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save(self, interaction: Interaction) -> None:
        schema_validation.validate_or_raise(interaction.to_schema_dict(), "interaction.schema.json")
        self._conn.execute(
            """
            INSERT INTO interactions (
                id, schema_version, created_at, source, channel_ref, raw_input,
                action_type, domain, due_at, agent_response,
                model_used, input_tokens, output_tokens, latency_ms, estimated_cost_usd, purpose,
                tool_executions_json,
                henry_correction, final_action_type, final_domain,
                task_status, related_interaction_id, closure_outcome, closed_at,
                next_check_at, last_checked_at, reminder_count, waiting_on
            ) VALUES (
                :id, :schema_version, :created_at, :source, :channel_ref, :raw_input,
                :action_type, :domain, :due_at, :agent_response,
                :model_used, :input_tokens, :output_tokens, :latency_ms, :estimated_cost_usd, :purpose,
                :tool_executions_json,
                :henry_correction, :final_action_type, :final_domain,
                :task_status, :related_interaction_id, :closure_outcome, :closed_at,
                :next_check_at, :last_checked_at, :reminder_count, :waiting_on
            )
            ON CONFLICT(id) DO UPDATE SET
                due_at=excluded.due_at,
                agent_response=excluded.agent_response,
                model_used=excluded.model_used,
                input_tokens=excluded.input_tokens,
                output_tokens=excluded.output_tokens,
                latency_ms=excluded.latency_ms,
                estimated_cost_usd=excluded.estimated_cost_usd,
                purpose=excluded.purpose,
                tool_executions_json=excluded.tool_executions_json,
                henry_correction=excluded.henry_correction,
                final_action_type=excluded.final_action_type,
                final_domain=excluded.final_domain,
                task_status=excluded.task_status,
                related_interaction_id=excluded.related_interaction_id,
                closure_outcome=excluded.closure_outcome,
                closed_at=excluded.closed_at,
                next_check_at=excluded.next_check_at,
                last_checked_at=excluded.last_checked_at,
                reminder_count=excluded.reminder_count,
                waiting_on=excluded.waiting_on
            """,
            {
                "id": interaction.id,
                "schema_version": interaction.schema_version,
                "created_at": interaction.created_at.isoformat(),
                "source": interaction.source,
                "channel_ref": interaction.channel_ref,
                "raw_input": interaction.raw_input,
                "action_type": interaction.action_type,
                "domain": interaction.domain,
                "due_at": interaction.due_at.isoformat() if interaction.due_at else None,
                "agent_response": interaction.agent_response,
                "model_used": interaction.model_used,
                "input_tokens": interaction.input_tokens,
                "output_tokens": interaction.output_tokens,
                "latency_ms": interaction.latency_ms,
                "estimated_cost_usd": interaction.estimated_cost_usd,
                "purpose": interaction.purpose,
                "tool_executions_json": json.dumps([t.to_schema_dict() for t in interaction.tool_executions]),
                "henry_correction": interaction.henry_correction,
                "final_action_type": interaction.final_action_type,
                "final_domain": interaction.final_domain,
                "task_status": interaction.task_status,
                "related_interaction_id": interaction.related_interaction_id,
                "closure_outcome": interaction.closure_outcome,
                "closed_at": interaction.closed_at.isoformat() if interaction.closed_at else None,
                "next_check_at": interaction.next_check_at.isoformat() if interaction.next_check_at else None,
                "last_checked_at": interaction.last_checked_at.isoformat() if interaction.last_checked_at else None,
                "reminder_count": interaction.reminder_count,
                "waiting_on": interaction.waiting_on,
            },
        )
        self._conn.commit()

    def _row_to_interaction(self, row: sqlite3.Row) -> Interaction:
        tool_executions = [
            ToolExecution(tool=t["tool"], summary=t["summary"], status=t.get("status", "success"))
            for t in json.loads(row["tool_executions_json"])
        ]
        return Interaction(
            id=row["id"],
            schema_version=row["schema_version"],
            created_at=_parse_dt(row["created_at"]),
            source=row["source"],
            channel_ref=row["channel_ref"],
            raw_input=row["raw_input"],
            action_type=row["action_type"],
            domain=row["domain"],
            due_at=_parse_dt(row["due_at"]),
            agent_response=row["agent_response"],
            model_used=row["model_used"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            latency_ms=row["latency_ms"],
            estimated_cost_usd=row["estimated_cost_usd"],
            purpose=row["purpose"],
            tool_executions=tool_executions,
            henry_correction=row["henry_correction"],
            final_action_type=row["final_action_type"],
            final_domain=row["final_domain"],
            task_status=row["task_status"],
            related_interaction_id=row["related_interaction_id"],
            closure_outcome=row["closure_outcome"],
            closed_at=_parse_dt(row["closed_at"]),
            next_check_at=_parse_dt(row["next_check_at"]),
            last_checked_at=_parse_dt(row["last_checked_at"]),
            reminder_count=row["reminder_count"] if row["reminder_count"] is not None else 0,
            waiting_on=row["waiting_on"],
        )

    def get(self, interaction_id: str) -> Optional[Interaction]:
        row = self._conn.execute(
            "SELECT * FROM interactions WHERE id = ?", (interaction_id,)
        ).fetchone()
        return self._row_to_interaction(row) if row is not None else None

    def all(self) -> Iterable[Interaction]:
        rows = self._conn.execute("SELECT * FROM interactions ORDER BY created_at ASC").fetchall()
        return [self._row_to_interaction(r) for r in rows]

    def open_by_action_type(self, action_type: str) -> Iterable[Interaction]:
        rows = self._conn.execute(
            """
            SELECT * FROM interactions
            WHERE task_status IN ('open', 'in_progress')
              AND (COALESCE(final_action_type, action_type) = ?)
            ORDER BY created_at ASC
            """,
            (action_type,),
        ).fetchall()
        return [self._row_to_interaction(r) for r in rows]

    def due_between(self, start: dt.datetime, end: dt.datetime) -> Iterable[Interaction]:
        rows = self._conn.execute(
            """
            SELECT * FROM interactions
            WHERE task_status IN ('open', 'in_progress')
              AND due_at IS NOT NULL AND due_at >= ? AND due_at < ?
            ORDER BY due_at ASC
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [self._row_to_interaction(r) for r in rows]

    def overdue(self, as_of: dt.datetime) -> Iterable[Interaction]:
        rows = self._conn.execute(
            """
            SELECT * FROM interactions
            WHERE task_status IN ('open', 'in_progress')
              AND due_at IS NOT NULL AND due_at < ?
            ORDER BY due_at ASC
            """,
            (as_of.isoformat(),),
        ).fetchall()
        return [self._row_to_interaction(r) for r in rows]

    def open_without_due_date(self) -> Iterable[Interaction]:
        rows = self._conn.execute(
            """
            SELECT * FROM interactions
            WHERE task_status IN ('open', 'in_progress') AND due_at IS NULL
            ORDER BY created_at ASC
            """
        ).fetchall()
        return [self._row_to_interaction(r) for r in rows]

    def due_for_check(self, as_of: dt.datetime) -> Iterable[Interaction]:
        rows = self._conn.execute(
            """
            SELECT * FROM interactions
            WHERE task_status IN ('open', 'in_progress')
              AND next_check_at IS NOT NULL AND next_check_at <= ?
            ORDER BY next_check_at ASC
            """,
            (as_of.isoformat(),),
        ).fetchall()
        return [self._row_to_interaction(r) for r in rows]
