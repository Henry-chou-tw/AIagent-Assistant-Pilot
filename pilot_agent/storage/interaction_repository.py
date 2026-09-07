"""Storage abstraction (Pilot Step 5): the rest of pilot_agent depends only
on this interface, never on SQLite directly. This is what keeps the data
architecture "not locked to Discord, not locked to Claude, not locked to a
single cloud provider, and migratable" -- swapping SqliteInteractionRepository
for a future Postgres/private-server-backed implementation should require
touching only this file's implementations, nothing that calls it.
"""
from __future__ import annotations

import abc
from typing import Iterable, Optional

from ..models import Interaction


class InteractionRepository(abc.ABC):
    @abc.abstractmethod
    def save(self, interaction: Interaction) -> None:
        """Insert or update (by id) one interaction record."""

    @abc.abstractmethod
    def get(self, interaction_id: str) -> Optional[Interaction]:
        ...

    @abc.abstractmethod
    def all(self) -> Iterable[Interaction]:
        ...

    @abc.abstractmethod
    def open_by_action_type(self, action_type: str) -> Iterable[Interaction]:
        """Interactions with this action_type (post-correction if
        corrected) whose task_status is 'open' or 'in_progress' -- e.g.
        for a future Daily-Close-style summary. Never guesses; returns
        an empty iterable when there are none."""
