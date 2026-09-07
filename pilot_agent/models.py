"""Domain model for the Assistant Pilot -- deliberately thin (Henry's
architecture constraint: this Pilot is not another platform, so this file
stays a plain mirror of schema/interaction.schema.json, never the
enforcement point itself; validate_or_raise() in schema_validation.py is
the actual enforcement, same discipline as the AIanger Platform's own
domain layer convention)."""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import List, Optional

ACTION_TYPES = (
    "todo",
    "reminder",
    "follow_up",
    "calendar",
    "idea",
    "knowledge",
    "decision_record",
    "general_ai_task",
    "unknown",
)

TASK_STATUSES = ("open", "in_progress", "done", "cancelled", "unknown")


@dataclasses.dataclass
class ToolExecution:
    tool: str
    summary: str
    status: str = "success"  # success | failed | skipped

    def to_schema_dict(self) -> dict:
        return {"tool": self.tool, "summary": self.summary, "status": self.status}


@dataclasses.dataclass
class Interaction:
    id: str
    created_at: dt.datetime
    source: str
    raw_input: str
    action_type: str
    task_status: str = "unknown"

    channel_ref: Optional[str] = None
    domain: Optional[str] = None
    agent_response: Optional[str] = None

    model_used: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    estimated_cost_usd: Optional[float] = None
    purpose: Optional[str] = None

    tool_executions: List[ToolExecution] = dataclasses.field(default_factory=list)

    henry_correction: Optional[str] = None
    final_action_type: Optional[str] = None
    final_domain: Optional[str] = None

    related_interaction_id: Optional[str] = None
    closure_outcome: Optional[str] = None
    closed_at: Optional[dt.datetime] = None

    schema_version: str = "1.0.0"

    def effective_action_type(self) -> str:
        """What Henry actually confirmed this was, falling back to the
        Agent's own initial guess when there was no correction. Callers
        that care about 'ground truth' (e.g. future rule-mining over
        Henry corrections) should use this, not action_type directly."""
        return self.final_action_type or self.action_type

    def effective_domain(self) -> Optional[str]:
        return self.final_domain or self.domain

    def to_schema_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "source": self.source,
            "channel_ref": self.channel_ref,
            "raw_input": self.raw_input,
            "action_type": self.action_type,
            "domain": self.domain,
            "agent_response": self.agent_response,
            "model_used": self.model_used,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "estimated_cost_usd": self.estimated_cost_usd,
            "purpose": self.purpose,
            "tool_executions": [t.to_schema_dict() for t in self.tool_executions],
            "henry_correction": self.henry_correction,
            "final_action_type": self.final_action_type,
            "final_domain": self.final_domain,
            "task_status": self.task_status,
            "related_interaction_id": self.related_interaction_id,
            "closure_outcome": self.closure_outcome,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }
