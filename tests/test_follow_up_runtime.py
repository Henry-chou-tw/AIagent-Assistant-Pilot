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
import json
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
from pilot_agent.followup_timing import (
    NEXT_CHECK_HINTS,
    NEXT_DAY_CHECK_HOUR,
    SAME_DAY_CUTOFF_HOUR,
    SAME_DAY_INTERVAL_HOURS,
    compute_next_check_at,
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
    """2026-09-07 Temporal Follow-up Reasoning Correction: the model only
    ever emits a qualitative next_check_hint; the concrete datetime is
    computed by followup_timing.py (tested separately, with a fixed
    clock, in TestFollowupTimingPolicy below). These tests exercise the
    real classify_and_respond() parsing/wiring, using the actual wall
    clock as "now" -- so assertions here check which calendar day the
    result lands on, not the exact time-of-day."""

    def _classify(self, raw_input, *, due_at=None, next_check_hint=None, waiting_on=None, action_type="follow_up"):
        payload = {
            "action_type": action_type,
            "domain": None,
            "due_at": due_at,
            "next_check_hint": next_check_hint,
            "waiting_on": waiting_on,
        }
        stdout = "(reply text)\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        return _FakeClaudeProvider(stdout).classify_and_respond(raw_input=raw_input)

    def test_klinggoo_same_day_hint_is_not_pushed_to_tomorrow(self):
        """The actual GPT-flagged FAIL case: a same_day hint must resolve
        to TODAY, never tomorrow, even though the same message also
        mentions "明日親送" for an unrelated (due_at) reason."""
        result = self._classify(
            "克靈固環境及食品消毒劑,廠商剛剛回覆,經理明日會親送過去,確切上午、下午我稍晚跟您說,幫我持續追蹤這一筆資料",
            due_at=None, next_check_hint="same_day", waiting_on="external",
        )
        self.assertIsNone(result.due_at, "due_at must stay null -- vendor gave no confirmed delivery time")
        self.assertIsNotNone(result.next_check_at, "same_day must produce a schedule, not null")
        computed = dt.datetime.fromisoformat(result.next_check_at)
        today = dt.datetime.now().astimezone().date()
        self.assertEqual(computed.date(), today, "same_day must land on today's calendar date, not tomorrow's")
        self.assertEqual(result.waiting_on, "external")

    def test_no_guessing_when_no_time_information_at_all(self):
        result = self._classify("隨手想法,沒有給任何時間", action_type="idea")
        self.assertIsNone(result.due_at)
        self.assertIsNone(result.next_check_at)
        self.assertIsNone(result.waiting_on)

    def test_unrecognized_hint_falls_back_to_no_schedule_never_crashes(self):
        result = self._classify("x", next_check_hint="tomorrow-ish-vaguely", waiting_on="henry")
        self.assertIsNone(result.next_check_at, "an unrecognized hint must never schedule anything, never guessed/repaired")
        self.assertEqual(result.waiting_on, "henry")

    def test_invalid_waiting_on_value_falls_back_to_none(self):
        result = self._classify("x", waiting_on="vendor")  # not one of henry/external
        self.assertIsNone(result.waiting_on, "waiting_on must only ever be henry/external/None, never passed through raw")

    def test_next_day_hint_lands_tomorrow_not_today(self):
        result = self._classify("客戶說明天再回覆報價", next_check_hint="next_day", waiting_on="external")
        computed = dt.datetime.fromisoformat(result.next_check_at)
        today = dt.datetime.now().astimezone().date()
        self.assertGreater(computed.date(), today, "next_day must not be scheduled for today")

    def test_later_hint_never_schedules_anything(self):
        result = self._classify("下週會確認", next_check_hint="later", waiting_on="external")
        self.assertIsNone(result.next_check_at, "a further-out ('later') promise must not force any auto-schedule")


class TestFollowupTimingPolicy(unittest.TestCase):
    """Pure, deterministic tests for pilot_agent/followup_timing.py --
    no model, no I/O, fixed clock (_now() from the top of this file)."""

    def test_same_day_normal_window(self):
        result = compute_next_check_at("same_day", _now())  # 09:00
        self.assertEqual(result, _now() + dt.timedelta(hours=SAME_DAY_INTERVAL_HOURS))
        self.assertEqual(result.date(), _now().date())

    def test_same_day_clamped_to_cutoff_never_rolls_to_tomorrow(self):
        late_now = _now(11)  # 20:00; +2.5h would be 22:30, past the 21:00 cutoff
        result = compute_next_check_at("same_day", late_now)
        self.assertEqual(result, late_now.replace(hour=SAME_DAY_CUTOFF_HOUR, minute=0, second=0, microsecond=0))
        self.assertEqual(result.date(), late_now.date())

    def test_same_day_after_cutoff_uses_short_fallback_never_crosses_midnight(self):
        very_late_now = _now(14.5)  # 23:30 -- already past the 21:00 cutoff
        result = compute_next_check_at("same_day", very_late_now)
        self.assertGreater(result, very_late_now)
        self.assertEqual(result.date(), very_late_now.date(), "must never roll into tomorrow's calendar date")

    def test_next_day_fixed_hour(self):
        result = compute_next_check_at("next_day", _now())
        expected = (_now() + dt.timedelta(days=1)).replace(hour=NEXT_DAY_CHECK_HOUR, minute=0, second=0, microsecond=0)
        self.assertEqual(result, expected)

    def test_later_and_none_and_unrecognized_produce_no_schedule(self):
        self.assertIsNone(compute_next_check_at("later", _now()))
        self.assertIsNone(compute_next_check_at(None, _now()))
        self.assertIsNone(compute_next_check_at("someday-ish", _now()))


class TestTemporalSemanticScenarios(unittest.TestCase):
    """Item 9 of Henry's correction: "稍後" and "明天" must never collapse
    into the same schedule bucket. NOTE (honest limitation): these tests
    supply the intended-correct next_check_hint directly in the canned
    model output -- they verify the CODE converts a given hint into the
    right schedule bucket. They do NOT verify the live `claude` CLI
    actually classifies this exact Chinese phrasing as that hint; this
    sandbox cannot invoke the real CLI (see the Closure Report)."""

    def _classify(self, raw_input, hint):
        payload = {"action_type": "follow_up", "domain": None, "due_at": None,
                   "next_check_hint": hint, "waiting_on": "external"}
        stdout = "(reply)\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        return _FakeClaudeProvider(stdout).classify_and_respond(raw_input=raw_input)

    def test_vendor_will_reply_price_later_today_is_same_day(self):
        result = self._classify("廠商晚點回覆價格", hint="same_day")
        computed = dt.datetime.fromisoformat(result.next_check_at)
        self.assertEqual(computed.date(), dt.datetime.now().astimezone().date())

    def test_colleague_will_confirm_stock_later_today_is_same_day(self):
        result = self._classify("同事稍後確認庫存", hint="same_day")
        computed = dt.datetime.fromisoformat(result.next_check_at)
        self.assertEqual(computed.date(), dt.datetime.now().astimezone().date())

    def test_client_will_reply_tomorrow_is_next_day_not_today(self):
        result = self._classify("客戶說明天再回覆", hint="next_day")
        computed = dt.datetime.fromisoformat(result.next_check_at)
        self.assertGreater(computed.date(), dt.datetime.now().astimezone().date())

    def test_confirm_next_week_does_not_become_todays_check(self):
        result = self._classify("下週會確認", hint="later")
        self.assertIsNone(result.next_check_at, "a further-out promise must not force any auto-schedule, same-day or otherwise")


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
    """克靈固環境及食品消毒劑,廠商剛剛回覆,經理明日會親送過去,確切上午、下午我
    稍晚跟您說,幫我持續追蹤這一筆資料 -- Henry's real example. 2026-09-07
    Temporal Follow-up Reasoning Correction: Stage A's next_check_at must
    now be SAME-DAY (GPT's independent verification FAILed the previous
    round for scheduling this "明天上午"/tomorrow morning instead)."""

    def setUp(self) -> None:
        super().setUp()
        os.environ["PILOT_DB_PATH"] = self._db_path

    def tearDown(self) -> None:
        os.environ.pop("PILOT_DB_PATH", None)
        super().tearDown()

    def test_full_two_stage_lifecycle(self):
        today = _now()  # 2026-09-07 09:00 +08:00 -- message received

        # --- Stage A: initial message classified. Per the corrected
        # system prompt, next_check_hint="same_day" (the "稍晚跟您說" cue),
        # NOT "next_day" just because "明日" also appears in the message
        # for the (separate, due_at-irrelevant) delivery-date mention.
        # Uses the real deterministic policy function directly, exactly
        # as claude_provider.py would after parsing that hint.
        stage_a_next_check_at = compute_next_check_at("same_day", today)
        print(f"\n[克靈固] Stage A created at {today.isoformat()}")
        print(f"[克靈固] Stage A due_at = None (vendor gave no confirmed time)")
        print(f"[克靈固] Stage A next_check_at = {stage_a_next_check_at.isoformat()} (SAME calendar day as {today.date()})")

        stage_a = self._make(
            id="interaction-klinggoo-1",
            created_at=today,
            raw_input="克靈固環境及食品消毒劑,廠商剛剛回覆,經理明日會親送過去,確切上午、下午我稍晚跟您說,幫我持續追蹤這一筆資料",
            action_type="follow_up",
            due_at=None,
            next_check_at=stage_a_next_check_at,
            waiting_on="external",
            task_status="open",
        )

        # Test A: Stage A's next_check_at must be same-day.
        self.assertEqual(stage_a_next_check_at.date(), today.date(),
                          "Stage A must be scheduled for TODAY, not tomorrow")

        # Not due yet at message-receipt time itself.
        self.assertEqual(list(self.repo.due_for_check(today)), [])

        # Test B: same-day checkpoint fires -- follow-up-watch must remind
        # about the still-missing AM/PM info (rather than staying silent
        # until tomorrow).
        sent = []
        outcomes = run_follow_up_watch(self.repo, stage_a_next_check_at, send=sent.append)
        print(f"[克靈固] follow-up-watch fired at {stage_a_next_check_at.isoformat()}, sent: {sent[0]!r}" if sent else "[克靈固] NOT fired")
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].interaction_id, stage_a.id)
        self.assertEqual(len(sent), 1)
        self.assertIn("克靈固", sent[0])

        # Test C: Henry replies same day with the AM/PM confirmation --
        # Stage A resolved, Stage B created (multi-stage handoff, same
        # mechanism as the previous round -- unaffected by this fix).
        henry_reply_time = today + dt.timedelta(hours=3)  # 12:00, same day
        tomorrow_3pm = (today + dt.timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        check_after_delivery = tomorrow_3pm + dt.timedelta(minutes=30)

        args = Namespace(
            id=stage_a.id,
            outcome="廠商確認明天下午 3 點送達",
            close_parent_status="done",
            next_input="確認克靈固消毒劑是否已送達",
            next_action_type="follow_up",
            next_due_at=tomorrow_3pm.isoformat(),
            next_check_at=check_after_delivery.isoformat(),
            next_waiting_on="external",
        )
        main_module.cmd_follow_up_advance(args)
        print(f"[克靈固] Henry replied at {henry_reply_time.isoformat()}: 廠商確認明天下午 3 點送達")

        parent = self.repo.get(stage_a.id)
        self.assertEqual(parent.task_status, "done")
        self.assertIsNone(parent.next_check_at, "resolved checkpoint must stop being watched")

        children = [r for r in self.repo.all() if r.related_interaction_id == stage_a.id]
        self.assertEqual(len(children), 1)
        stage_b = children[0]
        print(f"[克靈固] Stage B created: id={stage_b.id}")
        print(f"[克靈固] Stage B due_at = {stage_b.due_at.isoformat()} (Henry/vendor-confirmed delivery time)")
        print(f"[克靈固] Stage B next_check_at = {stage_b.next_check_at.isoformat()} (due_at + 30min)")
        print(f"[克靈固] Stage B waiting_on = {stage_b.waiting_on}")

        # Test D: Stage B's due_at is the real confirmed time; next_check_at
        # must be AFTER due_at (checking whether delivery actually happened).
        self.assertEqual(stage_b.task_status, "open")
        self.assertEqual(stage_b.due_at, tomorrow_3pm)
        self.assertEqual(stage_b.next_check_at, check_after_delivery)
        self.assertGreater(stage_b.next_check_at, stage_b.due_at)
        self.assertEqual(stage_b.waiting_on, "external")

        self.assertEqual(list(self.repo.due_for_check(tomorrow_3pm)), [])

        # Test E: Stage B's checkpoint fires after the confirmed delivery time.
        sent_b = []
        outcomes_b = run_follow_up_watch(self.repo, check_after_delivery, send=sent_b.append)
        print(f"[克靈固] follow-up-watch fired at {check_after_delivery.isoformat()}, sent: {sent_b[0]!r}" if sent_b else "[克靈固] NOT fired")
        self.assertEqual(len(outcomes_b), 1)
        self.assertEqual(outcomes_b[0].interaction_id, stage_b.id)

        # Test F: Henry confirms delivery arrived -- Stage B closed via the
        # normal `close` command (no further stage).
        main_module.cmd_close(Namespace(id=stage_b.id, status="done", outcome="已收到,確認送達"))
        closed = self.repo.get(stage_b.id)
        print(f"[克靈固] Stage B closed: task_status={closed.task_status}")
        self.assertEqual(closed.task_status, "done")

        # follow-up-watch must never pick either row up again.
        outcomes_final = run_follow_up_watch(self.repo, check_after_delivery + dt.timedelta(days=2), send=lambda m: None)
        fired_ids = {o.interaction_id for o in outcomes_final}
        self.assertNotIn(stage_a.id, fired_ids)
        self.assertNotIn(stage_b.id, fired_ids)

        # Daily Close at end of day 1: Stage A already resolved+closed
        # same day; Stage B is open but waiting_on='external' and not yet
        # due -- must not force a send.
        message, needs_henry = main_module._build_daily_close(self.repo, today + dt.timedelta(hours=10))
        print(f"[克靈固] Daily Close (day 1, 19:00) needs_henry_attention = {needs_henry}")
        self.assertFalse(needs_henry)


if __name__ == "__main__":
    unittest.main()
