"""Model Interface abstraction (Henry's Step 7 architecture constraint):
pilot_agent.intake_classifier depends only on this interface, never on
Claude directly. Claude is the first, not the only, provider -- a future
GPT or other provider implementation plugs in here without touching
intake_classifier.py or main.py."""
from __future__ import annotations

import abc
import dataclasses
from typing import Optional

from .models import ACTION_TYPES


@dataclasses.dataclass
class ModelResult:
    action_type: str  # one of ACTION_TYPES; provider must map anything else to "unknown"
    domain: Optional[str]
    response_text: str
    model_used: str
    due_at: Optional[str] = None  # ISO 8601 datetime string if the model could determine one; never fabricated
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    estimated_cost_usd: Optional[float] = None

    def __post_init__(self) -> None:
        if self.action_type not in ACTION_TYPES:
            self.action_type = "unknown"


class ModelInterface(abc.ABC):
    @abc.abstractmethod
    def classify_and_respond(self, *, raw_input: str, purpose: str = "intake_classification") -> ModelResult:
        """Classify raw_input into (action_type, domain) and produce a
        reply Henry will actually see. `purpose` is recorded verbatim on
        the Interaction record (Pilot Step 6) so later analysis can group
        invocations by why they were made, e.g. to find which purposes
        are simple enough to move to a deterministic rule or a smaller
        model."""
