"""Intake Classifier (Pilot Step 4/6).

v1 deliberately does NOT add any deterministic pre-filtering rules yet --
Henry's own instruction (Step 6): the first phase optimizes for correct
classification and capturing real usage behavior, not minimum token
spend. Every interaction goes through the Model Interface for now.

Once 1-2 months of real Interaction records (schema/interaction.schema.json,
specifically each record's `purpose`, `action_type` vs `final_action_type`
correction rate) exist, THIS is the file where a rules-first fast path
should be added -- e.g. "if raw_input matches this narrow pattern with a
near-100% historical correction-free rate, skip the model call". Do not
add such a rule before that evidence exists; that is exactly the kind of
premature optimization Henry's Step 6 explicitly asked to avoid.
"""
from __future__ import annotations

from .model_interface import ModelInterface, ModelResult


def classify(model: ModelInterface, raw_input: str) -> ModelResult:
    return model.classify_and_respond(raw_input=raw_input, purpose="intake_classification")
