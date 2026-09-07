# -*- coding: utf-8 -*-
"""Tests for the 2026-09-07 Contextual Follow-up Resolution milestone.

Run with (from the repo root, Windows):
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

Never touches data/pilot.db (temp SQLite per test, via RepoTestCase from
test_follow_up_runtime.py). Never invokes a live `claude` CLI call --
_ScriptedProvider below monkeypatches classify_and_respond/resolve_context
with a pre-programmed sequence of canned results, one per expected model
call, so a test can script a whole multi-turn conversation
deterministically.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import os
import unittest
from argparse import Namespace

from pilot_agent import main as main_module
from pilot_agent.context_resolution import apply_resolution, get_candidates
from pilot_agent.model_interface import ContextResolutionResult, ModelResult
from pilot_agent.providers.claude_provider import ClaudeProvider
from test_follow_up_runtime import RepoTestCase, _now  # noqa: reuse the existing fixed-clock test base


def _real_now() -> dt.datetime:
    """Real wall-clock 'now', used only where a test drives cmd_handle
    end-to-end (cmd_handle reads dt.datetime.now() internally -- it has
    no injectable clock -- so setup timestamps here are anchored to the
    real clock rather than the fixed _now() the lower-level tests use,
    to stay inside get_candidates' LOOKBACK_HOURS window regardless of
    when the test actually runs)."""
    return dt.datetime.now().astimezone()


class _ScriptedProvider(ClaudeProvider):
    """A ClaudeProvider whose classify_and_respond/resolve_context return
    a pre-programmed sequence of results instead of shelling out to any
    real CLI. `script` is a list of ("classify", ModelResult) or
    ("resolve", ContextResolutionResult) tuples, consumed in order --
    lets a test script an entire multi-message conversation."""

    def __init__(self, script):
        super().__init__()
        self._script = list(script)

    def classify_and_respond(self, *, raw_input, purpose="intake_classification"):
        kind, value = self._script.pop(0)
        assert kind == "classify", f"expected a classify() call next, script had {kind!r}"
        return value

    def resolve_context(self, *, raw_input, candidates, now_iso):
        kind, value = self._script.pop(0)
        assert kind == "resolve", f"expected a resolve_context() call next, script had {kind!r}"
        return value


def _run_handle(script, source, channel_ref, raw_input):
    """Runs cmd_handle with ClaudeProvider monkeypatched to the given
    script, capturing (and discarding) its stdout. Restores the real
    class afterward regardless of outcome."""
    original = main_module.ClaudeProvider
    main_module.ClaudeProvider = lambda: _ScriptedProvider(script)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            main_module.cmd_handle(Namespace(source=source, channel_ref=channel_ref, input=raw_input))
        return captured.getvalue()
    finally:
        main_module.ClaudeProvider = original


# ---------------------------------------------------------------------------
# 1. Candidate retrieval: deterministic, bounded, code-only
# ---------------------------------------------------------------------------

class TestCandidateRetrieval(RepoTestCase):
    def test_empty_when_nothing_open(self):
        self.assertEqual(get_candidates(self.repo, "下午會送", _now()), [])

    def test_topically_related_item_outranks_unrelated_one(self):
        self._make(id="interaction-klinggoo", raw_input="克靈固環境及食品消毒劑,廠商稍晚跟您說上午還是下午送",
                    action_type="follow_up", created_at=_now(-1), waiting_on="external")
        self._make(id="interaction-unrelated", raw_input="下週要準備公司旅遊行程規劃",
                    action_type="todo", created_at=_now(-1))
        candidates = get_candidates(self.repo, "下午會送", _now())
        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0].interaction_id, "interaction-klinggoo")

    def test_bounded_to_candidate_limit(self):
        from pilot_agent.context_resolution import CANDIDATE_LIMIT
        for i in range(CANDIDATE_LIMIT + 3):
            self._make(id=f"interaction-many-{i}", raw_input=f"追蹤事項第 {i} 筆,廠商稍後回覆",
                        action_type="follow_up", created_at=_now(-1))
        candidates = get_candidates(self.repo, "廠商稍後回覆", _now())
        self.assertLessEqual(len(candidates), CANDIDATE_LIMIT)

    def test_done_items_excluded(self):
        self._make(id="interaction-done", raw_input="克靈固已經處理完了", action_type="follow_up",
                    created_at=_now(-1), task_status="done")
        self.assertEqual(get_candidates(self.repo, "克靈固後續", _now()), [])

    def test_old_items_outside_lookback_excluded(self):
        from pilot_agent.context_resolution import LOOKBACK_HOURS
        self._make(id="interaction-old", raw_input="很久以前的克靈固追蹤", action_type="follow_up",
                    created_at=_now(-(LOOKBACK_HOURS + 24)))
        self.assertEqual(get_candidates(self.repo, "克靈固後續", _now()), [])

    def test_refs_are_local_not_real_ids(self):
        self._make(id="interaction-real-id-123", raw_input="克靈固消毒劑追蹤", action_type="follow_up", created_at=_now(-1))
        candidates = get_candidates(self.repo, "克靈固後續", _now())
        self.assertEqual(candidates[0].ref, "C1")
        self.assertNotEqual(candidates[0].ref, "interaction-real-id-123")


# ---------------------------------------------------------------------------
# 2. apply_resolution: mutation safety + confidence gate (unit level)
# ---------------------------------------------------------------------------

class TestApplyResolutionGates(RepoTestCase):
    def _candidate_for(self, interaction_id, ref="C1"):
        cands = get_candidates(self.repo, "任意訊息", _now())
        by_id = {c.interaction_id: c for c in cands}
        return by_id[interaction_id]

    def test_low_confidence_never_mutates(self):
        target = self._make(id="interaction-x", raw_input="克靈固消毒劑追蹤", action_type="follow_up",
                             created_at=_now(-1), waiting_on="external")
        candidates = get_candidates(self.repo, "下午會送", _now())
        resolution = ContextResolutionResult(resolution_type="CLOSE_EXISTING", candidate_ref="C1", confidence="medium", outcome_text="下午會送")
        outcome = apply_resolution(self.repo, resolution, candidates, raw_input="下午會送", source="discord", channel_ref="c1", now=_now())
        self.assertEqual(outcome.resolution_type, "AMBIGUOUS")
        unchanged = self.repo.get(target.id)
        self.assertEqual(unchanged.task_status, "open", "medium confidence must never mutate the target")

    def test_ambiguous_resolution_type_never_mutates_regardless_of_confidence(self):
        target = self._make(id="interaction-y", raw_input="克靈固消毒劑追蹤", action_type="follow_up",
                             created_at=_now(-1), waiting_on="external")
        candidates = get_candidates(self.repo, "下午會送", _now())
        resolution = ContextResolutionResult(resolution_type="AMBIGUOUS", candidate_ref="C1", confidence="high", ambiguous_refs=["C1"])
        apply_resolution(self.repo, resolution, candidates, raw_input="下午會送", source="discord", channel_ref="c1", now=_now())
        self.assertEqual(self.repo.get(target.id).task_status, "open")

    def test_invalid_candidate_ref_never_mutates_anything(self):
        """Simulates the model 'inventing' a ref that was never in the
        bounded candidate set -- mutation safety must reject it."""
        target = self._make(id="interaction-z", raw_input="克靈固消毒劑追蹤", action_type="follow_up",
                             created_at=_now(-1), waiting_on="external")
        candidates = get_candidates(self.repo, "下午會送", _now())
        resolution = ContextResolutionResult(resolution_type="CLOSE_EXISTING", candidate_ref="C99", confidence="high", outcome_text="下午會送")
        outcome = apply_resolution(self.repo, resolution, candidates, raw_input="下午會送", source="discord", channel_ref="c1", now=_now())
        self.assertEqual(outcome.resolution_type, "AMBIGUOUS")
        self.assertEqual(self.repo.get(target.id).task_status, "open")

    def test_already_closed_target_cannot_be_mutated_again(self):
        target = self._make(id="interaction-w", raw_input="克靈固消毒劑追蹤", action_type="follow_up",
                             created_at=_now(-1), waiting_on="external")
        candidates = get_candidates(self.repo, "下午會送", _now())
        target.task_status = "done"
        self.repo.save(target)
        resolution = ContextResolutionResult(resolution_type="CLOSE_EXISTING", candidate_ref="C1", confidence="high", outcome_text="下午會送")
        outcome = apply_resolution(self.repo, resolution, candidates, raw_input="下午會送", source="discord", channel_ref="c1", now=_now())
        self.assertEqual(outcome.resolution_type, "AMBIGUOUS", "a target that closed between retrieval and resolution must not be reopened/mutated")

    def test_high_confidence_close_existing_clears_next_check_at(self):
        target = self._make(id="interaction-v", raw_input="克靈固消毒劑追蹤", action_type="follow_up",
                             created_at=_now(-1), waiting_on="external", next_check_at=_now(+1))
        candidates = get_candidates(self.repo, "已送到", _now())
        resolution = ContextResolutionResult(resolution_type="CLOSE_EXISTING", candidate_ref="C1", confidence="high", outcome_text="已送到")
        outcome = apply_resolution(self.repo, resolution, candidates, raw_input="已送到", source="discord", channel_ref="c1", now=_now())
        self.assertEqual(outcome.resolution_type, "CLOSE_EXISTING")
        closed = self.repo.get(target.id)
        self.assertEqual(closed.task_status, "done")
        self.assertEqual(closed.closure_outcome, "已送到")
        self.assertIsNone(closed.next_check_at)


# ---------------------------------------------------------------------------
# 3. Acceptance cases (item 13, Henry's exact scenarios), full cmd_handle flow
# ---------------------------------------------------------------------------

class TestContextualResolutionAcceptanceCases(RepoTestCase):
    def setUp(self):
        super().setUp()
        os.environ["PILOT_DB_PATH"] = self._db_path

    def tearDown(self):
        os.environ.pop("PILOT_DB_PATH", None)
        super().tearDown()

    def test_case1_afternoon_delivery_auto_links_and_advances(self):
        stage_a = self._make(
            id="interaction-klinggoo-a", raw_input="克靈固環境及食品消毒劑,廠商剛剛回覆,經理明日會親送過去,確切上午、下午我稍晚跟您說,幫我持續追蹤這一筆資料",
            action_type="follow_up", created_at=_real_now() - dt.timedelta(hours=1), waiting_on="external",
        )
        script = [("resolve", ContextResolutionResult(
            resolution_type="ADVANCE_FOLLOW_UP", candidate_ref="C1", confidence="high",
            reason="Henry 回答了 Stage A 在等的上午/下午資訊", outcome_text="下午會送",
            due_at=None, next_check_hint="next_day",
        ))]
        _run_handle(script, "discord", "chan-1", "下午會送")

        parent = self.repo.get(stage_a.id)
        self.assertEqual(parent.task_status, "done", "Stage A must auto-resolve, Henry never named an interaction id")
        self.assertIsNone(parent.next_check_at)

        children = [i for i in self.repo.all() if i.related_interaction_id == stage_a.id]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].task_status, "open")
        self.assertEqual(children[0].resolution_type, "ADVANCE_FOLLOW_UP")

    def test_case2_delivered_message_auto_closes_stage_b(self):
        stage_b = self._make(
            id="interaction-klinggoo-b", raw_input="確認克靈固消毒劑是否已送達", action_type="follow_up",
            created_at=_real_now() - dt.timedelta(hours=1),
            due_at=_real_now() + dt.timedelta(hours=1), next_check_at=_real_now() + dt.timedelta(hours=1, minutes=30),
            waiting_on="external",
        )
        script = [("resolve", ContextResolutionResult(
            resolution_type="CLOSE_EXISTING", candidate_ref="C1", confidence="high",
            reason="Henry confirmed delivery", outcome_text="已送到",
        ))]
        _run_handle(script, "discord", "chan-1", "已送到")

        closed = self.repo.get(stage_b.id)
        self.assertEqual(closed.task_status, "done")
        self.assertIsNone(closed.next_check_at, "watch must stop firing once closed")

    def test_case3_two_plausible_candidates_is_ambiguous_neither_closed(self):
        klinggoo = self._make(id="interaction-klinggoo-wait", raw_input="克靈固消毒劑等待送達",
                               action_type="follow_up", created_at=_real_now() - dt.timedelta(hours=1), waiting_on="external")
        packaging = self._make(id="interaction-packaging-wait", raw_input="包材廠商等待送達",
                                action_type="follow_up", created_at=_real_now() - dt.timedelta(hours=1), waiting_on="external")
        script = [("resolve", ContextResolutionResult(
            resolution_type="AMBIGUOUS", confidence="low",
            reason="both candidates are equally plausible for a bare '已送到'",
            ambiguous_refs=["C1", "C2"],
        ))]
        output = _run_handle(script, "discord", "chan-1", "已送到")

        self.assertEqual(self.repo.get(klinggoo.id).task_status, "open", "must not close either candidate")
        self.assertEqual(self.repo.get(packaging.id).task_status, "open", "must not close either candidate")
        self.assertIn("還是", output, "response must ask a disambiguating question")

    def test_case4_shared_word_alone_must_not_falsely_link(self):
        klinggoo = self._make(id="interaction-klinggoo-c", raw_input="克靈固環境及食品消毒劑,廠商稍晚跟您說上午還是下午送",
                               action_type="follow_up", created_at=_real_now() - dt.timedelta(hours=1), waiting_on="external")
        script = [
            ("resolve", ContextResolutionResult(resolution_type="NEW_INTERACTION", confidence="high",
                                                  reason="different vendor/topic (packaging quote, not klinggoo delivery)")),
            ("classify", ModelResult(action_type="follow_up", domain="採購", response_text="好的,已經記錄包材廠商的報價。",
                                      model_used="claude-cli", next_check_at=None, waiting_on=None)),
        ]
        _run_handle(script, "discord", "chan-1", "包材廠商報價320")

        unchanged = self.repo.get(klinggoo.id)
        self.assertEqual(unchanged.task_status, "open", "克靈固 must not be touched just because 廠商 appears in both messages")
        self.assertIsNone(unchanged.related_interaction_id)

        newest = max(self.repo.all(), key=lambda i: i.created_at)
        self.assertIsNone(newest.related_interaction_id, "the new packaging-quote record must not be linked to klinggoo")

    def test_case5_explicit_lexical_match_closes_with_high_confidence(self):
        klinggoo = self._make(id="interaction-klinggoo-d", raw_input="克靈固消毒劑等待送達",
                               action_type="follow_up", created_at=_real_now() - dt.timedelta(hours=1), waiting_on="external")
        script = [("resolve", ContextResolutionResult(
            resolution_type="CLOSE_EXISTING", candidate_ref="C1", confidence="high",
            reason="explicit '克靈固' lexical match plus 已送到", outcome_text="克靈固已送到",
        ))]
        _run_handle(script, "discord", "chan-1", "克靈固已送到")

        self.assertEqual(self.repo.get(klinggoo.id).task_status, "done")

    def test_case6_specific_time_given_advances_with_real_due_at(self):
        stage_a = self._make(id="interaction-klinggoo-e", raw_input="克靈固消毒劑,廠商稍晚跟您說上午還是下午送",
                              action_type="follow_up", created_at=_real_now() - dt.timedelta(hours=1), waiting_on="external")
        tomorrow_3pm = (_real_now() + dt.timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        script = [("resolve", ContextResolutionResult(
            resolution_type="ADVANCE_FOLLOW_UP", candidate_ref="C1", confidence="high",
            reason="Henry gave a precise delivery time", outcome_text="明天下午3點送",
            due_at=tomorrow_3pm.isoformat(), next_check_hint=None,
        ))]
        _run_handle(script, "discord", "chan-1", "明天下午3點送")

        self.assertEqual(self.repo.get(stage_a.id).task_status, "done")
        children = [i for i in self.repo.all() if i.related_interaction_id == stage_a.id]
        self.assertEqual(len(children), 1)
        stage_b = children[0]
        self.assertEqual(stage_b.due_at, tomorrow_3pm, "due_at must be exactly what Henry gave, never invented")
        self.assertGreater(stage_b.next_check_at, stage_b.due_at, "next_check_at must be after due_at")

    def test_case7_no_open_followups_never_hallucinates_a_target(self):
        self.assertEqual(list(self.repo.all()), [])  # nothing open at all
        script = [("classify", ModelResult(action_type="follow_up", domain=None, response_text="好的,已經記錄。",
                                            model_used="claude-cli", next_check_at=None, waiting_on="external"))]
        _run_handle(script, "discord", "chan-1", "下午會送")

        all_rows = list(self.repo.all())
        self.assertEqual(len(all_rows), 1)
        self.assertIsNone(all_rows[0].related_interaction_id, "must not invent a link when there was nothing open to link to")
        self.assertIsNone(all_rows[0].resolution_type, "resolve_context is never even called when there are no candidates")


if __name__ == "__main__":
    unittest.main()
