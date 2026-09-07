"""Contextual Follow-up Resolution (2026-09-07 milestone).

The gap this closes: before this, Henry (or the live relay session on his
behalf) had to know an interaction's id and explicitly call
`follow-up-advance`/`correct`/`close` to update an open follow-up. A real
secretary doesn't work that way -- Henry should just be able to say "下午
會送" and have the Pilot figure out, on its own, that this updates the
"克靈固" tracking it already has open.

Pipeline (deliberately NOT "hand the model the whole database and let it
decide anything"; see item 9 of Henry's instructions):

    SQLite deterministic retrieval (get_candidates, this module)
        -> bounded candidate list (<= CANDIDATE_LIMIT, code-scored)
        -> structured model decision (ModelInterface.resolve_context)
        -> code validation + confidence gate (apply_resolution, this module)
        -> mutation (only if the gate passes)

Every mutation step re-validates its inputs from scratch (target still
exists, still open, candidate ref came from the bounded set this specific
call produced) -- the model's own claims are never trusted as sufficient
by themselves. This is deliberate defense in depth, not paranoia: Henry's
explicit instruction is that a False Link (wrongly closing/advancing the
wrong item) is worse than a Missed Link (asking one clarifying question),
so every gate defaults to the safe side.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable, List, Optional

from .ids import generate_id
from .model_interface import CandidateSummary, ContextResolutionResult
from .models import Interaction

CANDIDATE_LIMIT = 5
LOOKBACK_HOURS = 72  # "bounded recent open context" -- item 6

_EXCERPT_LEN = 60


def _excerpt(text: Optional[str], length: int = _EXCERPT_LEN) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:length] + ("…" if len(text) > length else "")


def _char_bigrams(text: str) -> set:
    text = (text or "").strip()
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _lexical_overlap(a: str, b: str) -> float:
    """Jaccard similarity over character bigrams -- a simple, deterministic,
    dependency-free similarity measure that works reasonably on Chinese
    text without a real tokenizer/segmenter (none is installed in this
    environment). Not NLP-grade, just enough to rank candidates before
    the model looks at them; the model still makes the actual judgement
    call using full context, not just this score."""
    set_a, set_b = _char_bigrams(a), _char_bigrams(b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def _last_activity_at(interaction: Interaction) -> dt.datetime:
    if interaction.last_checked_at is not None:
        return max(interaction.last_checked_at, interaction.created_at)
    return interaction.created_at


def _recency_score(interaction: Interaction, now: dt.datetime) -> float:
    hours_since = max((now - _last_activity_at(interaction)).total_seconds() / 3600.0, 0.0)
    return 1.0 / (1.0 + hours_since / 24.0)


def _waiting_proximity_score(interaction: Interaction, now: dt.datetime) -> float:
    """A small bonus for items whose next_check_at is close to now (in
    either direction) -- exactly the moment Henry would naturally be
    replying to a reminder the Pilot already sent."""
    if interaction.next_check_at is None:
        return 0.0
    hours_away = abs((interaction.next_check_at - now).total_seconds() / 3600.0)
    return 1.0 / (1.0 + hours_away / 12.0)


def _score(interaction: Interaction, raw_input: str, now: dt.datetime) -> float:
    lexical = _lexical_overlap(raw_input, interaction.raw_input or "")
    recency = _recency_score(interaction, now)
    waiting = _waiting_proximity_score(interaction, now)
    # Weights are a simple, documented, deterministic default -- not
    # tuned on real usage data yet (see the Closure Report's honest
    # limitations). Lexical overlap dominates because topical relevance
    # matters more than recency alone (an old but topically-matching item
    # should still outrank a brand new unrelated one).
    return 0.5 * lexical + 0.2 * recency + 0.3 * waiting


def get_candidates(repo, raw_input: str, now: dt.datetime, limit: int = CANDIDATE_LIMIT) -> List[CandidateSummary]:
    """Deterministic, code-only retrieval -- no model call. Returns at
    most `limit` candidates, highest-scored first, each given a throwaway
    local ref ("C1", "C2", ...). Returns [] when there is nothing open and
    recent to consider -- callers should skip resolve_context() entirely
    in that case (item 6/7: no candidates -> straight to a normal new
    interaction, same as before this milestone)."""
    since = now - dt.timedelta(hours=LOOKBACK_HOURS)
    open_items = list(repo.open_trackable_since(since))
    if not open_items:
        return []

    scored = sorted(open_items, key=lambda i: _score(i, raw_input, now), reverse=True)
    top = scored[:limit]

    return [
        CandidateSummary(
            ref=f"C{idx + 1}",
            interaction_id=item.id,
            raw_input_excerpt=_excerpt(item.raw_input),
            action_type=item.effective_action_type(),
            domain=item.effective_domain(),
            waiting_on=item.waiting_on,
            due_at=item.due_at.isoformat() if item.due_at else None,
            next_check_at=item.next_check_at.isoformat() if item.next_check_at else None,
        )
        for idx, item in enumerate(top)
    ]


class ResolutionOutcome:
    def __init__(self, interaction_id: str, action_type: str, resolution_type: str, response_text: str):
        self.interaction_id = interaction_id
        self.action_type = action_type
        self.resolution_type = resolution_type
        self.response_text = response_text


def _validated_iso_datetime(raw_value: Optional[str]) -> Optional[str]:
    if not raw_value:
        return None
    try:
        dt.datetime.fromisoformat(raw_value)
        return raw_value
    except (ValueError, TypeError):
        return None


def _ambiguous_question(candidates_by_ref: dict, refs: List[str]) -> str:
    # Item 5: never dump a long list -- show at most the top 3 the model
    # flagged as competing (code enforces the cap, not the model).
    shown = [candidates_by_ref[r] for r in refs if r in candidates_by_ref][:3]
    if not shown:
        return "不確定你是在說哪一筆之前的追蹤,可以再說明一下嗎?"
    options = "、".join(f"「{c.raw_input_excerpt}」" for c in shown)
    return f"你說的是 {options} 這其中哪一筆的後續,還是另外一件事?麻煩再確認一下。"


def _log_audit_interaction(
    repo,
    *,
    raw_input: str,
    source: str,
    channel_ref: Optional[str],
    now: dt.datetime,
    resolution: ContextResolutionResult,
    related_interaction_id: Optional[str],
    response_text: str,
    task_status: str,
) -> Interaction:
    """Every resolution decision -- including AMBIGUOUS and every rejected
    mutation attempt -- produces exactly one Interaction record (Pilot's
    existing data-ownership principle: every real interaction produces a
    record). This IS the audit trail (item 11): raw_input is Henry's
    original message verbatim, related_interaction_id (when set) is what
    it got linked to, resolution_type/confidence/reason capture the
    decision. Henry can later correct a wrong link with the existing
    `correct` command against this record's id (item 12) -- see the
    Closure Report for why that's sufficient without a new undo engine."""
    record = Interaction(
        id=generate_id("interaction"),
        created_at=now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc),
        source=source,
        channel_ref=channel_ref,
        raw_input=raw_input,
        action_type="follow_up",
        task_status=task_status,
        agent_response=response_text,
        purpose=f"context_resolution:{resolution.resolution_type}",
        related_interaction_id=related_interaction_id,
        resolution_type=resolution.resolution_type,
        resolution_confidence=resolution.confidence,
        resolution_reason=resolution.reason,
    )
    repo.save(record)
    return record


def apply_resolution(
    repo,
    resolution: ContextResolutionResult,
    candidates: List[CandidateSummary],
    *,
    raw_input: str,
    source: str,
    channel_ref: Optional[str],
    now: dt.datetime,
) -> ResolutionOutcome:
    """The mutation-safety gate (item 10) + confidence gate (item 8),
    enforced entirely in code -- resolution.resolution_type and
    resolution.confidence are inputs to be validated, never trusted
    outright. Falls back to a safe AMBIGUOUS-style log-and-ask for every
    case that doesn't clear every check."""
    from .followup_timing import compute_next_check_at  # local import: keep this module's import graph shallow

    candidates_by_ref = {c.ref: c for c in candidates}

    def ambiguous(reason_suffix: str, refs: Optional[List[str]] = None) -> ResolutionOutcome:
        refs = refs or resolution.ambiguous_refs or list(candidates_by_ref.keys())
        question = _ambiguous_question(candidates_by_ref, refs)
        record = _log_audit_interaction(
            repo,
            raw_input=raw_input,
            source=source,
            channel_ref=channel_ref,
            now=now,
            resolution=resolution,
            related_interaction_id=None,  # deliberately NOT linked -- we are not committing to any candidate
            response_text=question,
            task_status="unknown",
        )
        return ResolutionOutcome(
            interaction_id=record.id,
            action_type="unknown",
            resolution_type="AMBIGUOUS",
            response_text=question,
        )

    # --- Confidence gate (item 8): enforced regardless of what
    # resolution_type claims. Only high-confidence, non-ambiguous
    # resolutions are allowed to mutate anything.
    if resolution.resolution_type == "AMBIGUOUS" or resolution.confidence != "high":
        return ambiguous("below confidence gate")

    if resolution.resolution_type not in ("UPDATE_EXISTING", "ADVANCE_FOLLOW_UP", "CLOSE_EXISTING"):
        return ambiguous(f"unrecognized resolution_type {resolution.resolution_type!r}")

    # --- Mutation safety (item 10): candidate ref must come from the
    # bounded set THIS call produced -- the model cannot invent an id.
    candidate = candidates_by_ref.get(resolution.candidate_ref or "")
    if candidate is None:
        return ambiguous("candidate_ref did not match the bounded candidate set")

    target = repo.get(candidate.interaction_id)
    if target is None or target.task_status not in ("open", "in_progress"):
        return ambiguous("target interaction no longer open/available")

    outcome_text = resolution.outcome_text or raw_input
    validated_due_at = _validated_iso_datetime(resolution.due_at)
    hint = resolution.next_check_hint if resolution.next_check_hint in ("same_day", "next_day", "later") else None

    if resolution.resolution_type == "CLOSE_EXISTING":
        target.task_status = resolution.close_status if resolution.close_status in ("done", "cancelled") else "done"
        target.closure_outcome = outcome_text
        target.closed_at = now
        target.next_check_at = None  # item 4: stop any further auto-checking once closed
        repo.save(target)

        response_text = f"好的,已經把「{candidate.raw_input_excerpt}」標記完成。"
        record = _log_audit_interaction(
            repo, raw_input=raw_input, source=source, channel_ref=channel_ref, now=now,
            resolution=resolution, related_interaction_id=target.id,
            response_text=response_text, task_status="done",
        )
        return ResolutionOutcome(record.id, "follow_up", "CLOSE_EXISTING", response_text)

    if resolution.resolution_type == "ADVANCE_FOLLOW_UP":
        target.task_status = "done"
        target.closure_outcome = outcome_text
        target.closed_at = now
        target.next_check_at = None
        repo.save(target)

        if validated_due_at is not None:
            due_at_dt = dt.datetime.fromisoformat(validated_due_at)
            next_check_at = due_at_dt + dt.timedelta(minutes=30)
        else:
            # Item 3: no precise time given -- never invent one. Use the
            # deterministic hint policy instead (documented simplification:
            # a coarse "下午" answer without an exact hour still maps
            # through the existing same_day/next_day taxonomy rather than
            # a dedicated coarse-time representation -- see Closure Report).
            due_at_dt = None
            next_check_at = compute_next_check_at(hint, now)

        child = Interaction(
            id=generate_id("interaction"),
            created_at=now,
            source=source,
            channel_ref=channel_ref,
            raw_input=raw_input,
            action_type="follow_up",
            task_status="open",
            due_at=due_at_dt,
            next_check_at=next_check_at,
            waiting_on="external",
            purpose="context_resolution:ADVANCE_FOLLOW_UP",
            related_interaction_id=target.id,
            resolution_type=resolution.resolution_type,
            resolution_confidence=resolution.confidence,
            resolution_reason=resolution.reason,
        )
        repo.save(child)

        response_text = f"好的,已經記錄「{candidate.raw_input_excerpt}」的最新進度,會繼續幫你追蹤後續。"
        # Update the audit fields on the record we already created (the
        # child IS the audit record here -- one write, not two).
        return ResolutionOutcome(child.id, "follow_up", "ADVANCE_FOLLOW_UP", response_text)

    # UPDATE_EXISTING: refresh timing without closing/advancing a stage.
    if hint is not None:
        target.next_check_at = compute_next_check_at(hint, now)
    repo.save(target)

    response_text = f"好的,已經更新「{candidate.raw_input_excerpt}」的追蹤狀態。"
    record = _log_audit_interaction(
        repo, raw_input=raw_input, source=source, channel_ref=channel_ref, now=now,
        resolution=resolution, related_interaction_id=target.id,
        response_text=response_text, task_status="open",
    )
    return ResolutionOutcome(record.id, "follow_up", "UPDATE_EXISTING", response_text)
