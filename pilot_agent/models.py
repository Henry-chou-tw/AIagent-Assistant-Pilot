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

# 2026-09-07 (Follow-up Runtime Closure round): who/what this interaction is
# currently blocked on, if anything. Deliberately separate from task_status --
# task_status says open/in_progress/done; waiting_on says *why* it's still
# open, so Daily Close can stop treating every open item as "stuck on Henry"
# (Henry's explicit complaint about the v1 Daily Close). None/null is a
# legitimate, honest value when this isn't determinable or doesn't apply --
# never defaulted to "henry" just because it's easier.
WAITING_ON_VALUES = ("henry", "external")

# 2026-09-07 (Contextual Follow-up Resolution milestone): what cmd_handle
# decided this interaction record represents, when it went through the
# candidate-retrieval + structured-resolution pipeline in
# context_resolution.py. RESOLUTION_TYPES mirrors the pipeline's possible
# outcomes; None means this record predates the milestone or never went
# through resolution (e.g. there were no open candidates to consider).
RESOLUTION_TYPES = ("NEW_INTERACTION", "UPDATE_EXISTING", "ADVANCE_FOLLOW_UP", "CLOSE_EXISTING", "AMBIGUOUS")
RESOLUTION_CONFIDENCE_VALUES = ("high", "medium", "low")


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
    due_at: Optional[dt.datetime] = None  # deadline/reminder/calendar time if stated; never invented (see schema)

    # 2026-09-07 (Follow-up Runtime Closure round): due_at is the thing's OWN
    # time (a deadline Henry/the world stated). next_check_at is a *different*
    # axis -- when the SYSTEM should next actively check/remind on this item.
    # These must never be conflated (GPT's independent-verification finding):
    # e.g. "廠商明天親送,上午/下午稍後補充" has due_at=None (no confirmed
    # delivery time to invent) but next_check_at can still legitimately be
    # set to "tomorrow morning" -- that's the Pilot deciding when to go ask
    # again, not a claim about when the delivery itself will happen.
    next_check_at: Optional[dt.datetime] = None
    last_checked_at: Optional[dt.datetime] = None  # last time follow-up-watch actually fired on this row
    reminder_count: int = 0  # how many times follow-up-watch has nagged about this row; caps the backoff, see follow_up_watch.py
    waiting_on: Optional[str] = None  # "henry" | "external" | None -- see WAITING_ON_VALUES above

    # 2026-09-07 (Contextual Follow-up Resolution milestone): audit trail
    # for automatic candidate-resolution decisions (see
    # pilot_agent/context_resolution.py). None for records that never went
    # through the resolution pipeline. This is deliberately a minimal
    # audit trail (decision + confidence + reason), not full event
    # sourcing with before/after diffs -- see context_resolution.py's
    # module docstring for the honest scope call.
    resolution_type: Optional[str] = None  # one of RESOLUTION_TYPES, or None
    resolution_confidence: Optional[str] = None  # one of RESOLUTION_CONFIDENCE_VALUES, or None
    resolution_reason: Optional[str] = None  # short free-text explanation from the resolution model call

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
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "next_check_at": self.next_check_at.isoformat() if self.next_check_at else None,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "reminder_count": self.reminder_count,
            "waiting_on": self.waiting_on,
            "resolution_type": self.resolution_type,
            "resolution_confidence": self.resolution_confidence,
            "resolution_reason": self.resolution_reason,
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
