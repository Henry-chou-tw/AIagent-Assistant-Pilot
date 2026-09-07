"""Model Interface abstraction (Henry's Step 7 architecture constraint):
pilot_agent.intake_classifier depends only on this interface, never on
Claude directly. Claude is the first, not the only, provider -- a future
GPT or other provider implementation plugs in here without touching
intake_classifier.py or main.py."""
from __future__ import annotations

import abc
import dataclasses
from typing import List, Optional

from .models import ACTION_TYPES, RESOLUTION_TYPES


@dataclasses.dataclass
class ModelResult:
    action_type: str  # one of ACTION_TYPES; provider must map anything else to "unknown"
    domain: Optional[str]
    response_text: str
    model_used: str
    due_at: Optional[str] = None  # ISO 8601 datetime string if the model could determine one; never fabricated
    next_check_at: Optional[str] = None  # ISO 8601 string: when the Pilot should next check/remind on this; a scheduling choice, not a fact about the world (see models.py)
    waiting_on: Optional[str] = None  # "henry" | "external" | None -- see models.WAITING_ON_VALUES
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    estimated_cost_usd: Optional[float] = None

    def __post_init__(self) -> None:
        if self.action_type not in ACTION_TYPES:
            self.action_type = "unknown"


@dataclasses.dataclass
class CandidateSummary:
    """One bounded, code-retrieved candidate handed to resolve_context()
    (2026-09-07, Contextual Follow-up Resolution milestone). `ref` is a
    LOCAL, throwaway label (e.g. "C1") assigned by context_resolution.py's
    retrieval step -- never a real interaction id. The model only ever
    sees/returns refs, never ids; context_resolution.py is the only place
    that maps a ref back to a real interaction id, and it validates that
    mapping before any mutation (see apply_resolution's mutation-safety
    checks). This is what makes "the model cannot invent an interaction
    id" an enforced property, not just a prompt instruction."""
    ref: str
    interaction_id: str
    raw_input_excerpt: str
    action_type: str
    domain: Optional[str]
    waiting_on: Optional[str]
    due_at: Optional[str]
    next_check_at: Optional[str]


@dataclasses.dataclass
class ContextResolutionResult:
    """Structured output of resolve_context() -- deliberately NO free-text
    reply field. The reply Henry actually sees is always templated by
    context_resolution.py from real candidate/interaction data, never
    generated freely by this call, so a wrong resolution can never smuggle
    fabricated content into what Henry reads (see apply_resolution)."""
    resolution_type: str  # one of models.RESOLUTION_TYPES
    candidate_ref: Optional[str] = None  # e.g. "C1"; required for everything except NEW_INTERACTION/AMBIGUOUS
    ambiguous_refs: List[str] = dataclasses.field(default_factory=list)  # for AMBIGUOUS: the competing refs
    confidence: str = "low"  # "high" | "medium" | "low"
    reason: str = ""
    outcome_text: Optional[str] = None  # Henry's own words describing what changed, for closure_outcome
    due_at: Optional[str] = None  # ISO 8601, ONLY if Henry gave a genuinely precise time; never invented
    next_check_hint: Optional[str] = None  # "same_day" | "next_day" | "later" | None -- see followup_timing.py
    close_status: str = "done"  # "done" | "cancelled", for CLOSE_EXISTING

    def __post_init__(self) -> None:
        if self.resolution_type not in RESOLUTION_TYPES:
            self.resolution_type = "AMBIGUOUS"
        if self.confidence not in ("high", "medium", "low"):
            self.confidence = "low"


class ModelInterface(abc.ABC):
    @abc.abstractmethod
    def classify_and_respond(self, *, raw_input: str, purpose: str = "intake_classification") -> ModelResult:
        """Classify raw_input into (action_type, domain) and produce a
        reply Henry will actually see. `purpose` is recorded verbatim on
        the Interaction record (Pilot Step 6) so later analysis can group
        invocations by why they were made, e.g. to find which purposes
        are simple enough to move to a deterministic rule or a smaller
        model."""

    @abc.abstractmethod
    def resolve_context(
        self, *, raw_input: str, candidates: List[CandidateSummary], now_iso: str
    ) -> ContextResolutionResult:
        """2026-09-07, Contextual Follow-up Resolution milestone. Given a
        new message and a bounded, already-retrieved list of candidate
        open follow-ups (never the whole database -- context_resolution.py
        does that retrieval deterministically before this is ever
        called), decide whether this message is a brand new interaction,
        an update/advance/closure of one of the given candidates, or
        genuinely ambiguous between two or more of them. Only called when
        candidates is non-empty -- an empty candidate list never reaches
        here (falls straight through to classify_and_respond, same as
        before this milestone)."""
