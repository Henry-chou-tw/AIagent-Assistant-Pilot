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

    @abc.abstractmethod
    def due_between(self, start, end) -> Iterable[Interaction]:
        """Open/in_progress interactions with due_at in [start, end).
        Powers the 08:15 Morning Brief's "today" lists (Functional Spec
        v1.0 SS5.1). start/end are timezone-aware datetimes."""

    @abc.abstractmethod
    def overdue(self, as_of) -> Iterable[Interaction]:
        """Open/in_progress interactions with due_at < as_of. Powers
        Morning Brief's "逾期 Todo" and Daily Close's summary."""

    @abc.abstractmethod
    def open_without_due_date(self) -> Iterable[Interaction]:
        """Open/in_progress interactions with due_at still null -- e.g.
        Morning Brief's "尚未排時間的重要 Todo"."""

    @abc.abstractmethod
    def due_for_check(self, as_of) -> Iterable[Interaction]:
        """Open/in_progress interactions with next_check_at IS NOT NULL
        AND next_check_at <= as_of. Powers follow-up-watch (2026-09-07,
        Follow-up Runtime Closure round) -- the hourly job that actively
        re-checks/reminds on follow-ups, as opposed to due_at which just
        powers the twice-daily Morning Brief/Daily Close summaries. Never
        returns done/cancelled rows even if their next_check_at was left
        stale from before closure."""
