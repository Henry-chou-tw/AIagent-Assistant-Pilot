# -*- coding: utf-8 -*-
"""Tests for the 2026-09-07 Follow-up Runtime Closure round.

Run with (from the repo root, Windows):
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

These tests never touch data/pilot.db -- every test that needs a database
creates a fresh temp SQLite file and points PILOT_DB_PATH at it (see
setUp/tearDown in RepoTestCase). They never invoke a live `claude` CLI
call or a real Discord API call either -- ClaudeProvider tests monkeypatch
_invoke_cli(), and follow-up-watch tests inject a recording `send`
callable instead of the real notifications.send_message.
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from argparse import Namespace
from types import SimpleNamespace

from pilot_agent import main as main_module
from pilot_agent.follow_up_watch import (
    MAX_AUTO_REMINDERS,
    RETRY_INTERVAL,
    run_follow_up_watch,
)
from pilot_agent.model_interface import ModelResult
from pilot_agent.models import Interaction
from pilot_agent.providers.claude_provider import ClaudeProvider
from pilot_agent.storage import db as db_module
from pilot_agent.storage.sqlite_interaction_repository import SqliteInteractionRepository

TZ = dt.timezone(dt.timedelta(hours=8))  # matches Henry's locale (UTC+8), arbitrary but fixed for determinism


def _now(offset_hours: float = 0) -> dt.datetime:
    return dt.datetime(2026, 9, 7, 9, 0, 0, tzinfo=TZ) + dt.timedelta(hours=offset_hours)


class RepoTestCase(unittest.TestCase):
    """Base class: fresh temp SQLite DB per test, never data/pilot.db."""

    def setUp(self) -> None:
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self._db_path)  # let connect() create it fresh
        conn = db_module.connect(self._db_path)
        db_module.initialize_schema(conn)
        self.repo = SqliteInteractionRepository(conn)

    def tearDown(self) -> None:
        if os.path.exists(self._db_path):
            os.remove(self._db_path)

    def _make(self, **overrides) -> Interaction:
        defaults = dict(
            id=overrides.pop("id", None) or f"interaction-test{id(overrides)}",
            created_at=_now(-1),
            source="cli",
            raw_input="test raw input",
            action_type="follow_up",
            task_status="open",
        )
        defaults.update(overrides)
        interaction = Interaction(**defaults)
        self.repo.save(interaction)
        return interaction


# ---------------------------------------------------------------------------
# 1. Classifier contract: due_at vs next_check_at not conflated, no guessing
# ---------------------------------------------------------------------------

class _FakeClaudeProvider(ClaudeProvider):
    """Real ClaudeProvider with _invoke_cli monkeypatched -- exercises the
    real JSON-parsing/validation code in classify_and_respond() without
    shelling out to any actual CLI or model."""

    def __init__(self, canned_stdout: str):
        super().__init__()
        self._canned_stdout = canned_stdout

    def _invoke_cli(self, prompt: str):
        return self._canned_stdout, 100, 50, 0.001


class TestClassifierDueAtVsNextCheckAt(unittest.TestCase):
    def test_due_at_and_next_check_at_not_conflated(self):
        stdout = (
            "廠商說明天親送,確切時段稍後補充,我會持續追蹤。\n"
            "```json\n"
            '{"action_type": "follow_up", "domain": null, "due_at": null, '
            '"next_check_at": "2026-09-08T09:00:00+08:00", "waiting_on": "external"}\n'
            "```"
        )
        result = _FakeClaudeProvider(stdout).classify_and_respond(raw_input="克靈固消毒劑追蹤")
        self.assertIsNone(result.due_at, "due_at must stay null when the vendor gave no confirmed time")
        self.assertEqual(result.next_check_at, "2026-09-08T09:00:00+08:00")
        self.assertEqual(result.waiting_on, "external")

    def test_no_guessing_when_no_time_information_at_all(self):
        stdout = (
            "記錄下來了。\n"
            "```json\n"
            '{"action_type": "idea", "domain": null, "due_at": null, '
            '"next_check_at": null, "waiting_on": null}\n'
            "```"
        )
        result = _FakeClaudeProvider(stdout).classify_and_respond(raw_input="隨手想法,沒有給任何時間")
        self.assertIsNone(result.due_at)
        self.assertIsNone(result.next_check_at)
        self.assertIsNone(result.waiting_on)

    def test_malformed_next_check_at_falls_back_to_none_never_crashes(self):
        stdout = (
            "好的。\n"
            "```json\n"
            '{"action_type": "todo", "domain": null, "due_at": null, '
            '"next_check_at": "not-a-real-date", "waiting_on": "henry"}\n'
            "```"
        )
        result = _FakeClaudeProvider(stdout).classify_and_respond(raw_input="x")
        self.assertIsNone(result.next_check_at, "an unparseable next_check_at must be dropped, never passed through")
        self.assertEqual(result.waiting_on, "henry")

    def test_invalid_waiting_on_value_falls_back_to_none(self):
        stdout = (
            "好的。\n"
            "```json\n"
            '{"action_type": "follow_up", "domain": null, "due_at": null, '
            '"next_check_at": null, "waiting_on": "vendor"}\n'  # not one of henry/external
            "```"
        )
        result = _FakeClaudeProvider(stdout).classify_and_respond(raw_input="x")
        self.assertIsNone(result.waiting_on, "waiting_on must only ever be henry/external/None, never passed through raw")


# ---------------------------------------------------------------------------
# 2. Repository: due_for_check semantics
# ---------------------------------------------------------------------------

class TestDueForCheck(RepoTestCase):
    def test_only_items_with_next_check_at_le_now_are_returned(self):
        past_due = self._make(id="interaction-past", next_check_at=_now(-2))
        future = self._make(id="interaction-future", next_check_at=_now(+2))
        never_scheduled = self._make(id="interaction-none", next_check_at=None)

        due = list(self.repo.due_for_check(_now()))
        due_ids = {i.id for i in due}

        self.assertIn(past_due.id, due_ids)
        self.assertNotIn(future.id, due_ids, "next_check_at in the future must not fire yet")
        self.assertNotIn(never_scheduled.id, due_ids, "null next_check_at must never be treated as due")

    def test_done_and_cancelled_excluded_even_if_next_check_at_is_past(self):
        done_item = self._make(id="interaction-done", next_check_at=_now(-1), task_status="done")
        cancelled_item = self._make(id="interaction-cancelled", next_check_at=_now(-1), task_status="cancelled")
        still_open = self._make(id="interaction-open", next_check_at=_now(-1), task_status="in_progress")

        due_ids = {i.id for i in self.repo.due_for_check(_now())}
        self.assertNotIn(done_item.id, due_ids, "done items must never be nagged about")
        self.assertNotIn(cancelled_item.id, due_ids, "cancelled items must never be nagged about")
        self.assertIn(still_open.id, due_ids)


# ---------------------------------------------------------------------------
# 3. follow_up_watch: backoff / anti-spam behaviour
# ---------------------------------------------------------------------------

class TestFollowUpWatchBackoff(RepoTestCase):
    def _recorder(self):
        sent = []

        def send(message: str) -> None:
            sent.append(message)

        return send, sent

    def test_silent_when_nothing_due(self):
        send, sent = self._recorder()
        outcomes = run_follow_up_watch(self.repo, _now(), send=send)
        self.assertEqual(outcomes, [])
        self.assertEqual(sent, [], "no messages should be sent when nothing is due")

    def test_fires_once_and_reschedules_forward(self):
        item = self._make(id="interaction-watch1", next_check_at=_now(-1))
        send, sent = self._recorder()

        outcomes = run_follow_up_watch(self.repo, _now(), send=send)

        self.assertEqual(len(sent), 1)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].reminder_count_after, 1)
        self.assertFalse(outcomes[0].backed_off)

        updated = self.repo.get(item.id)
        self.assertEqual(updated.reminder_count, 1)
        self.assertEqual(updated.last_checked_at, _now())
        self.assertEqual(updated.next_check_at, _now() + RETRY_INTERVAL,
                          "must be rescheduled forward, not left at the same past timestamp")

    def test_backs_off_after_max_reminders_and_stops_auto_firing(self):
        item = self._make(
            id="interaction-watch2",
            next_check_at=_now(-1),
            reminder_count=MAX_AUTO_REMINDERS - 1,
            waiting_on="external",
        )
        send, sent = self._recorder()

        outcomes = run_follow_up_watch(self.repo, _now(), send=send)
        self.assertEqual(len(sent), 1)
        self.assertTrue(outcomes[0].backed_off)

        updated = self.repo.get(item.id)
        self.assertEqual(updated.reminder_count, MAX_AUTO_REMINDERS)
        self.assertIsNone(updated.next_check_at, "must stop scheduling further auto-reminders once the cap is hit")
        self.assertEqual(updated.waiting_on, "henry", "exhausted auto-nagging should escalate to needing Henry")

        # Second run, moments later: must NOT fire again (this is the actual
        # "never spams the same item indefinitely" guarantee).
        send2, sent2 = self._recorder()
        outcomes2 = run_follow_up_watch(self.repo, _now(5), send=send2)
        self.assertEqual(sent2, [], "backed-off item with next_check_at=None must never fire again")
        self.assertEqual(outcomes2, [])

    def test_never_fires_repeatedly_within_the_retry_interval(self):
        """Simulates the hourly Task Scheduler cadence: firing the watch
        again one hour later (well within RETRY_INTERVAL=6h) must NOT
        re-notify the same item -- this is the literal "hourly job must
        not spam" scenario Henry described."""
        self._make(id="interaction-watch3", next_check_at=_now(-1))
        send, sent = self._recorder()
        run_follow_up_watch(self.repo, _now(), send=send)
        self.assertEqual(len(sent), 1)

        send_next_hour, sent_next_hour = self._recorder()
        run_follow_up_watch(self.repo, _now(+1), send=send_next_hour)
        self.assertEqual(sent_next_hour, [], "must not re-fire again just one hour later")


# ---------------------------------------------------------------------------
# 4. Daily Close bucketing: not everything open == blocked on Henry
# ---------------------------------------------------------------------------

class TestDailyCloseBucketing(RepoTestCase):
    def test_purely_external_items_do_not_need_henry_attention(self):
        self._make(id="interaction-ext", waiting_on="external", due_at=None)
        message, needs_henry = main_module._build_daily_close(self.repo, _now())
        self.assertFalse(needs_henry, "an item only waiting on something external must not force a send")
        self.assertIn("純資訊", message)

    def test_henry_item_forces_attention_flag(self):
        self._make(id="interaction-henry", waiting_on="henry", due_at=None)
        message, needs_henry = main_module._build_daily_close(self.repo, _now())
        self.assertTrue(needs_henry)
        self.assertIn("需要你補資訊/決定", message)

    def test_legacy_unclassified_items_conservatively_still_flagged(self):
        """Pre-migration rows have waiting_on=None (the column didn't exist
        yet) -- must not be silently dropped, but also must not be
        mislabeled as either bucket."""
        self._make(id="interaction-legacy", waiting_on=None, due_at=None)
        message, needs_henry = main_module._build_daily_close(self.repo, _now())
        self.assertTrue(needs_henry, "unclassified legacy rows should conservatively still surface")
        self.assertIn("尚未分類", message)

    def test_mixed_bucket_only_external_present_is_not_all_treated_as_blocked(self):
        self._make(id="interaction-ext1", waiting_on="external", due_at=None)
        self._make(id="interaction-ext2", waiting_on="external", due_at=None)
        message, needs_henry = main_module._build_daily_close(self.repo, _now())
        self.assertFalse(needs_henry, "two external-only items must still not force a send")


# ---------------------------------------------------------------------------
# 5. End-to-end: the real 克靈固消毒劑 case
# ---------------------------------------------------------------------------

class TestKlinggooDisinfectantCase(RepoTestCase):
    """克靈固環境及食品消毒劑,廠商回覆經理明日會親送,確切上午、下午稍晚補充,
    幫我持續追蹤這一筆資料 -- Henry's real example, from message intake all
    the way through a two-stage checkpoint and closure."""

    def setUp(self) -> None:
        super().setUp()
        os.environ["PILOT_DB_PATH"] = self._db_path

    def tearDown(self) -> None:
        os.environ.pop("PILOT_DB_PATH", None)
        super().tearDown()

    def test_full_two_stage_lifecycle(self):
        today = _now()  # 2026-09-07 09:00 +08:00
        tomorrow_morning = _now(+24)  # 2026-09-08 09:00 +08:00

        # --- Stage A: initial message classified, matches the classifier
        # contract test above (due_at=None, next_check_at=tomorrow morning,
        # waiting_on='external') -- constructed directly here since the
        # classifier's own correctness is covered by TestClassifierDueAtVsNextCheckAt.
        stage_a = self._make(
            id="interaction-klinggoo-1",
            created_at=today,
            raw_input="克靈固環境及食品消毒劑,廠商回覆經理明日會親送,確切上午、下午稍晚補充,幫我持續追蹤這一筆資料",
            action_type="follow_up",
            due_at=None,
            next_check_at=tomorrow_morning,
            waiting_on="external",
            task_status="open",
        )

        # Not due yet today.
        self.assertEqual(list(self.repo.due_for_check(today)), [])

        # --- Tomorrow morning: follow-up-watch fires stage A's checkpoint.
        sent = []
        outcomes = run_follow_up_watch(self.repo, tomorrow_morning, send=sent.append)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].interaction_id, stage_a.id)
        self.assertEqual(len(sent), 1)
        self.assertIn("克靈固", sent[0])

        # --- Henry replies: vendor confirmed this afternoon 3pm delivery.
        # The live relay session would call follow-up-advance; we call the
        # CLI handler directly here (same code path Task Scheduler / the
        # relay session actually invokes).
        this_afternoon = _now(+24 + 6)  # tomorrow 15:00
        check_after_delivery = _now(+24 + 6.5)  # tomorrow 15:30

        args = Namespace(
            id=stage_a.id,
            outcome="廠商確認明天(即今天)下午 3 點送達",
            close_parent_status="done",
            next_input="確認克靈固消毒劑是否已送達",
            next_action_type="follow_up",
            next_due_at=this_afternoon.isoformat(),
            next_check_at=check_after_delivery.isoformat(),
            next_waiting_on="external",
        )
        main_module.cmd_follow_up_advance(args)

        parent = self.repo.get(stage_a.id)
        self.assertEqual(parent.task_status, "done")
        self.assertIsNone(parent.next_check_at, "resolved checkpoint must stop being watched")

        all_rows = list(self.repo.all())
        children = [r for r in all_rows if r.related_interaction_id == stage_a.id]
        self.assertEqual(len(children), 1)
        stage_b = children[0]
        self.assertEqual(stage_b.task_status, "open")
        self.assertEqual(stage_b.due_at, this_afternoon,
                          "stage B's due_at is legitimately known now -- it came from Henry/vendor, not invented")
        self.assertEqual(stage_b.next_check_at, check_after_delivery)
        self.assertEqual(stage_b.waiting_on, "external")

        # --- Not due yet the moment it's created.
        self.assertEqual(list(self.repo.due_for_check(this_afternoon)), [])

        # --- Stage B's checkpoint fires after 15:30.
        sent_b = []
        outcomes_b = run_follow_up_watch(self.repo, check_after_delivery, send=sent_b.append)
        self.assertEqual(len(outcomes_b), 1)
        self.assertEqual(outcomes_b[0].interaction_id, stage_b.id)

        # --- Henry confirms delivery arrived; item closed via the normal
        # `close` command (no further stage).
        main_module.cmd_close(Namespace(id=stage_b.id, status="done", outcome="已收到,確認送達"))
        closed = self.repo.get(stage_b.id)
        self.assertEqual(closed.task_status, "done")

        # --- follow-up-watch must never pick either row up again.
        outcomes_final = run_follow_up_watch(self.repo, check_after_delivery + dt.timedelta(days=2), send=lambda m: None)
        fired_ids = {o.interaction_id for o in outcomes_final}
        self.assertNotIn(stage_a.id, fired_ids)
        self.assertNotIn(stage_b.id, fired_ids)

        # --- Daily Close at end of day 1: stage A already resolved+closed
        # (task_status='done', excluded from open queries); nothing open
        # yet needing Henry (stage B not created until later that day in
        # this scenario's timeline, but even if it were open, it's
        # waiting_on='external' and not yet due -- shouldn't force a send).
        message, needs_henry = main_module._build_daily_close(self.repo, today + dt.timedelta(hours=10))
        self.assertFalse(needs_henry)


if __name__ == "__main__":
    unittest.main()
