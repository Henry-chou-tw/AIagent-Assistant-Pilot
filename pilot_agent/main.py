"""CLI entrypoint (Pilot Step 7): this is the ONLY thing the Discord ->
Channels -> live Claude Code session should call when a Discord message
comes in, per CLAUDE.md's instructions. The live session's job is
transport relay only (receive from Discord, invoke this, relay the
printed response back to Discord via the discord plugin) -- the actual
classification, model invocation, and data persistence all happen inside
this deterministic script, not inside the live session's own free-form
reasoning. This is what keeps data capture reliable even though the live
session itself is not fully deterministic (see the Pilot kickoff
discussion's point on recording reliability).

Usage:
    python -m pilot_agent.main handle --source discord --channel-ref <id> --input "..."
    python -m pilot_agent.main correct --id <interaction_id> --correction "..." [--final-action-type ...] [--final-domain ...]
    python -m pilot_agent.main close --id <interaction_id> --status done --outcome "..."
    python -m pilot_agent.main list-open --action-type todo
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from .config import database_path
from .context_resolution import apply_resolution, get_candidates
from .follow_up_watch import run_follow_up_watch
from .ids import generate_id
from .intake_classifier import classify
from .models import ACTION_TYPES, TASK_STATUSES, WAITING_ON_VALUES, Interaction
from .notifications import DiscordPushError, send_message
from .providers.claude_provider import ClaudeProvider
from .storage import db as db_module
from .storage.sqlite_interaction_repository import SqliteInteractionRepository


def _repository() -> SqliteInteractionRepository:
    conn = db_module.connect(database_path())
    db_module.initialize_schema(conn)
    return SqliteInteractionRepository(conn)


def _create_new_interaction(
    repo, model, *, source, channel_ref, raw_input,
    resolution_type=None, resolution_confidence=None, resolution_reason=None,
) -> Interaction:
    """The original (pre-2026-09-07-milestone) `handle` behaviour: run
    intake classification and persist a brand new Interaction. Reused for
    both "there were no open candidates at all" and "resolve_context
    itself decided this is NEW_INTERACTION" -- the resolution_* fields are
    only non-None in the latter case, giving an honest audit trail either
    way (see context_resolution.py's module docstring)."""
    result = classify(model, raw_input)

    due_at = dt.datetime.fromisoformat(result.due_at) if result.due_at else None
    next_check_at = dt.datetime.fromisoformat(result.next_check_at) if result.next_check_at else None
    waiting_on = result.waiting_on if result.waiting_on in WAITING_ON_VALUES else None

    interaction = Interaction(
        id=generate_id("interaction"),
        created_at=dt.datetime.now(dt.timezone.utc),
        source=source,
        channel_ref=channel_ref,
        raw_input=raw_input,
        action_type=result.action_type,
        domain=result.domain,
        due_at=due_at,
        next_check_at=next_check_at,
        waiting_on=waiting_on,
        agent_response=result.response_text,
        model_used=result.model_used,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
        estimated_cost_usd=result.estimated_cost_usd,
        purpose="intake_classification",
        task_status="open" if result.action_type != "unknown" else "unknown",
        resolution_type=resolution_type,
        resolution_confidence=resolution_confidence,
        resolution_reason=resolution_reason,
    )
    repo.save(interaction)
    return interaction


def cmd_handle(args: argparse.Namespace) -> None:
    """2026-09-07, Contextual Follow-up Resolution milestone: before
    treating this as a brand new message, check whether it's actually
    updating/advancing/closing an open follow-up the Pilot already has
    (see context_resolution.py). Henry never has to name an interaction
    id or pick a command by hand for the common case -- `handle` is still
    the only thing CLAUDE.md's relay session ever needs to call."""
    repo = _repository()
    model = ClaudeProvider()
    now = dt.datetime.now().astimezone()

    candidates = get_candidates(repo, args.input, now)

    if not candidates:
        # Item 6/7: nothing open and recent to consider -- straight to
        # normal classification, exactly as before this milestone.
        interaction = _create_new_interaction(repo, model, source=args.source, channel_ref=args.channel_ref, raw_input=args.input)
        print(json.dumps({"id": interaction.id, "action_type": interaction.action_type, "domain": interaction.domain}))
        print("---")
        print(interaction.agent_response)
        return

    resolution = model.resolve_context(raw_input=args.input, candidates=candidates, now_iso=now.isoformat())

    if resolution.resolution_type == "NEW_INTERACTION":
        interaction = _create_new_interaction(
            repo, model, source=args.source, channel_ref=args.channel_ref, raw_input=args.input,
            resolution_type=resolution.resolution_type,
            resolution_confidence=resolution.confidence,
            resolution_reason=resolution.reason,
        )
        print(json.dumps({"id": interaction.id, "action_type": interaction.action_type, "domain": interaction.domain}))
        print("---")
        print(interaction.agent_response)
        return

    # UPDATE_EXISTING / ADVANCE_FOLLOW_UP / CLOSE_EXISTING / AMBIGUOUS all
    # go through the mutation-safety + confidence gate in apply_resolution
    # -- it re-validates everything itself and never trusts resolution_type
    # or confidence at face value.
    outcome = apply_resolution(
        repo, resolution, candidates,
        raw_input=args.input, source=args.source, channel_ref=args.channel_ref, now=now,
    )
    print(json.dumps({"id": outcome.interaction_id, "action_type": outcome.action_type, "resolution_type": outcome.resolution_type}))
    print("---")
    print(outcome.response_text)


def cmd_correct(args: argparse.Namespace) -> None:
    repo = _repository()
    interaction = repo.get(args.id)
    if interaction is None:
        print(f"error: no interaction with id {args.id}", file=sys.stderr)
        sys.exit(1)

    interaction.henry_correction = args.correction
    if args.final_action_type:
        if args.final_action_type not in ACTION_TYPES:
            print(f"error: final-action-type must be one of {ACTION_TYPES}", file=sys.stderr)
            sys.exit(1)
        interaction.final_action_type = args.final_action_type
    if args.final_domain:
        interaction.final_domain = args.final_domain
    repo.save(interaction)
    print(f"corrected {args.id}")


def cmd_close(args: argparse.Namespace) -> None:
    repo = _repository()
    interaction = repo.get(args.id)
    if interaction is None:
        print(f"error: no interaction with id {args.id}", file=sys.stderr)
        sys.exit(1)
    if args.status not in TASK_STATUSES:
        print(f"error: status must be one of {TASK_STATUSES}", file=sys.stderr)
        sys.exit(1)

    interaction.task_status = args.status
    interaction.closure_outcome = args.outcome
    if args.status in ("done", "cancelled"):
        interaction.closed_at = dt.datetime.now(dt.timezone.utc)
    repo.save(interaction)
    print(f"closed {args.id} as {args.status}")


def cmd_follow_up_advance(args: argparse.Namespace) -> None:
    """Advance a multi-stage follow-up to its next checkpoint (2026-09-07,
    Follow-up Runtime Closure round -- Henry's item 3: 克靈固消毒劑 case).

    Deliberately reuses the existing related_interaction_id field (no new
    table): this closes the CURRENT interaction (the checkpoint that just
    got resolved, e.g. "明天上午/下午確認了") and, if --next-input is
    given, creates a brand-new linked Interaction for the NEXT checkpoint
    (e.g. "確認實際送達"), with its own independent due_at/next_check_at/
    waiting_on -- never overwriting or reusing the parent's due_at for a
    different real-world fact. follow-up-watch doesn't need to know
    anything about "stages"; it just sees another open row with its own
    next_check_at."""
    repo = _repository()
    parent = repo.get(args.id)
    if parent is None:
        print(f"error: no interaction with id {args.id}", file=sys.stderr)
        sys.exit(1)

    parent.task_status = args.close_parent_status
    parent.closure_outcome = args.outcome
    parent.next_check_at = None  # this checkpoint is resolved; stop watch from re-firing on it
    if args.close_parent_status in ("done", "cancelled"):
        parent.closed_at = dt.datetime.now(dt.timezone.utc)
    repo.save(parent)
    print(f"advanced {args.id}: closed as {args.close_parent_status} ({args.outcome})")

    if not args.next_input:
        return

    next_due_at = dt.datetime.fromisoformat(args.next_due_at) if args.next_due_at else None
    next_check_at = dt.datetime.fromisoformat(args.next_check_at) if args.next_check_at else None
    next_waiting_on = args.next_waiting_on if args.next_waiting_on in WAITING_ON_VALUES else None

    child = Interaction(
        id=generate_id("interaction"),
        created_at=dt.datetime.now(dt.timezone.utc),
        source=parent.source,
        channel_ref=parent.channel_ref,
        raw_input=args.next_input,
        action_type=args.next_action_type,
        domain=parent.domain,
        due_at=next_due_at,
        next_check_at=next_check_at,
        waiting_on=next_waiting_on,
        agent_response=None,
        purpose="follow_up_advance",
        task_status="open",
        related_interaction_id=parent.id,
    )
    repo.save(child)
    print(f"created follow-up checkpoint {child.id} (related_interaction_id={parent.id})")


def cmd_follow_up_watch(args: argparse.Namespace) -> None:
    """Hourly Task Scheduler entrypoint (2026-09-07, Follow-up Runtime
    Closure round -- Henry's item 5/6): the REAL follow-up due-check,
    independent of the fixed 08:15/22:00 summaries. See
    follow_up_watch.py for the anti-spam backoff design."""
    repo = _repository()
    now = dt.datetime.now().astimezone()

    if args.no_send:
        def sender(message: str) -> None:
            print("--- (--no-send,以下訊息未實際發送到 Discord) ---")
            print(message)
    else:
        sender = None  # run_follow_up_watch defaults this to the real notifications.send_message

    outcomes = run_follow_up_watch(repo, now, send=sender)

    if not outcomes:
        print("沒有到期需要追蹤的項目,沒有發送任何訊息。")
        return

    for outcome in outcomes:
        tag = "(已達提醒上限,已停止自動追蹤,轉為需要 Henry 關注)" if outcome.backed_off else ""
        print(f"{outcome.interaction_id}\t第 {outcome.reminder_count_after} 次提醒\t{outcome.raw_input_excerpt} {tag}")


def cmd_list_open(args: argparse.Namespace) -> None:
    repo = _repository()
    for interaction in repo.open_by_action_type(args.action_type):
        print(f"{interaction.id}\t{interaction.created_at.isoformat()}\t{interaction.raw_input[:80]}")


def _brief_line(interaction, *, show_due: bool = True) -> str:
    label = (interaction.raw_input or "").strip().replace("\n", " ")
    if len(label) > 80:
        label = label[:80] + "…"
    if show_due and interaction.due_at is not None:
        return f"- ({interaction.due_at.strftime('%H:%M')}) {label}"
    return f"- {label}"


def _build_morning_brief(repo, now: dt.datetime) -> str:
    """Functional Spec v1.0 SS5.1: 今日 Calendar / 今日 Todo / 今日 Reminder /
    逾期 Todo（保留原執行日期）/ 尚未排時間的重要 Todo. Pilot v1 simplification:
    no distinct Agent-result section (Pilot doesn't dispatch Agents)."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + dt.timedelta(days=1)

    due_today = list(repo.due_between(today_start, today_end))
    overdue_items = list(repo.overdue(today_start))
    no_date_open = [
        i for i in repo.open_without_due_date()
        if i.effective_action_type() in ("todo", "reminder", "follow_up")
    ]

    def of_type(items, action_type):
        return [i for i in items if i.effective_action_type() == action_type]

    sections = []
    sections.append(f"**08:15 今日行程** — {now.strftime('%Y-%m-%d (%a)')}")

    calendar_today = of_type(due_today, "calendar")
    if calendar_today:
        sections.append("**今日 Calendar**\n" + "\n".join(_brief_line(i) for i in calendar_today))

    todo_today = of_type(due_today, "todo")
    if todo_today:
        sections.append("**今日 Todo**\n" + "\n".join(_brief_line(i) for i in todo_today))

    reminder_today = of_type(due_today, "reminder")
    if reminder_today:
        sections.append("**今日 Reminder**\n" + "\n".join(_brief_line(i) for i in reminder_today))

    other_today = [i for i in due_today if i.effective_action_type() not in ("calendar", "todo", "reminder")]
    if other_today:
        sections.append("**今日其他事項**\n" + "\n".join(_brief_line(i) for i in other_today))

    if overdue_items:
        sections.append(
            "**逾期（保留原執行日期）**\n"
            + "\n".join(f"- (原訂 {i.due_at.strftime('%Y-%m-%d %H:%M')}) "
                         f"{(i.raw_input or '').strip()[:70]}" for i in overdue_items)
        )

    if no_date_open:
        sections.append(
            "**尚未排時間的待辦／提醒／追蹤**\n"
            + "\n".join(_brief_line(i, show_due=False) for i in no_date_open)
        )

    if len(sections) == 1:
        sections.append("今天沒有排定的事項,也沒有逾期或未排時間的待辦。")

    return "\n\n".join(sections)


def _bucket_by_waiting_on(items):
    """Split open items into (henry, external, unclassified) per
    Interaction.waiting_on (2026-09-07, Follow-up Runtime Closure round --
    Henry's item 7: Daily Close must stop treating every open item as
    "stuck on Henry"). unclassified (waiting_on is None) covers rows from
    before this field existed, or ones the classifier genuinely couldn't
    determine -- kept visible (conservative, matches old behaviour)
    rather than silently dropped, but reported honestly as unclassified,
    not mislabeled as either bucket."""
    henry_items, external_items, unclassified_items = [], [], []
    for item in items:
        if item.waiting_on == "henry":
            henry_items.append(item)
        elif item.waiting_on == "external":
            external_items.append(item)
        else:
            unclassified_items.append(item)
    return henry_items, external_items, unclassified_items


def _build_daily_close(repo, now: dt.datetime):
    """Functional Spec v1.0 SS5.3, Pilot v1 minimal-viable version
    (2026-09-07, Follow-up Runtime Closure round). Still not the full
    spec's Open Loop object type (no separate schema object, no richer
    blocked-reason taxonomy) -- that's an honestly-disclosed limitation,
    not built here. What THIS round adds: items are bucketed by
    waiting_on so a purely "waiting on some vendor" item no longer reads
    as "blocked on Henry", and the Discord push is now conditional --
    it only actually sends when something in the needs-Henry bucket is
    non-empty. Returns (message_text, needs_henry_attention) so the
    caller can implement that conditional send without re-deriving the
    bucketing logic."""
    overdue_items = list(repo.overdue(now))
    no_date_open = [
        i for i in repo.open_without_due_date()
        if i.effective_action_type() in ("todo", "reminder", "follow_up")
    ]
    all_open = overdue_items + no_date_open

    henry_items, external_items, unclassified_items = _bucket_by_waiting_on(all_open)
    needs_henry_attention = bool(henry_items) or bool(unclassified_items)

    sections = [f"**22:00 今日總結** — {now.strftime('%Y-%m-%d (%a)')}"]

    def _line(i):
        if i.due_at is not None:
            return f"- (原訂 {i.due_at.strftime('%Y-%m-%d %H:%M')}) {(i.raw_input or '').strip()[:70]}"
        return _brief_line(i, show_due=False)

    if not all_open:
        sections.append("今天沒有卡住的事項,也沒有逾期項目。")
    else:
        if henry_items:
            sections.append("**需要你補資訊/決定**\n" + "\n".join(_line(i) for i in henry_items))
        if unclassified_items:
            sections.append(
                "**尚未分類（沿用舊行為誠實列出，非本輪判斷範圍）**\n"
                + "\n".join(_line(i) for i in unclassified_items)
            )
        if external_items:
            sections.append(
                "**純資訊：等待外部回覆/事件中（不需要你現在處理）**\n"
                + "\n".join(_line(i) for i in external_items)
            )
        if not needs_henry_attention:
            sections.append("以上都只是等外部而已,今天沒有真的卡住你的事項——這則不會實際發送到 Discord。")

    sections.append(
        "（註：這仍是 Pilot v1 的簡化版——還沒有規格書 SS5.3 原本設計的完整 Open Loop 物件"
        "類型,但已經會依 waiting_on 區分「等外部」跟「卡住你」,只在真的有事項需要你注意時才發送。）"
    )
    return "\n\n".join(sections), needs_henry_attention


def cmd_morning_brief(args: argparse.Namespace) -> None:
    repo = _repository()
    now = dt.datetime.now().astimezone()
    message = _build_morning_brief(repo, now)
    print(message)
    if args.no_send:
        return
    try:
        send_message(message)
    except DiscordPushError as exc:
        print(f"[錯誤] 發送到 Discord 失敗: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_daily_close(args: argparse.Namespace) -> None:
    repo = _repository()
    now = dt.datetime.now().astimezone()
    message, needs_henry_attention = _build_daily_close(repo, now)
    print(message)
    if args.no_send:
        return
    if not needs_henry_attention:
        # Conditional Daily Close (Henry's item 7): nothing in this run
        # actually needs his attention (open items, if any, are all
        # purely waiting on something external) -- don't push a Discord
        # message just to say "nothing's wrong". This is the difference
        # from the old v1 behaviour, which always sent a snapshot.
        print("（沒有需要 Henry 注意的事項,本次不發送 Discord 訊息。）")
        return
    try:
        send_message(message)
    except DiscordPushError as exc:
        print(f"[錯誤] 發送到 Discord 失敗: {exc}", file=sys.stderr)
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pilot_agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_handle = sub.add_parser("handle")
    p_handle.add_argument("--source", required=True)
    p_handle.add_argument("--channel-ref", default=None)
    p_handle.add_argument("--input", required=True)
    p_handle.set_defaults(func=cmd_handle)

    p_correct = sub.add_parser("correct")
    p_correct.add_argument("--id", required=True)
    p_correct.add_argument("--correction", required=True)
    p_correct.add_argument("--final-action-type", default=None)
    p_correct.add_argument("--final-domain", default=None)
    p_correct.set_defaults(func=cmd_correct)

    p_close = sub.add_parser("close")
    p_close.add_argument("--id", required=True)
    p_close.add_argument("--status", required=True)
    p_close.add_argument("--outcome", default=None)
    p_close.set_defaults(func=cmd_close)

    p_list = sub.add_parser("list-open")
    p_list.add_argument("--action-type", required=True)
    p_list.set_defaults(func=cmd_list_open)

    p_morning = sub.add_parser("morning-brief", help="Build (and by default send) the 08:15 today's-schedule summary")
    p_morning.add_argument("--no-send", action="store_true", help="Print only, don't post to Discord (for manual testing)")
    p_morning.set_defaults(func=cmd_morning_brief)

    p_close = sub.add_parser("daily-close", help="Build (and by default send) the 22:00 end-of-day summary")
    p_close.add_argument("--no-send", action="store_true", help="Print only, don't post to Discord (for manual testing)")
    p_close.set_defaults(func=cmd_daily_close)

    p_watch = sub.add_parser(
        "follow-up-watch",
        help="Hourly Task Scheduler entrypoint: check next_check_at, send reminders, back off (never spams)",
    )
    p_watch.add_argument("--no-send", action="store_true", help="Print reminder text instead of posting to Discord (for manual testing)")
    p_watch.set_defaults(func=cmd_follow_up_watch)

    p_advance = sub.add_parser(
        "follow-up-advance",
        help="Resolve one follow-up checkpoint and optionally create the next linked checkpoint (multi-stage tracking)",
    )
    p_advance.add_argument("--id", required=True, help="id of the interaction/checkpoint being resolved")
    p_advance.add_argument("--outcome", required=True, help="What was learned/resolved, verbatim")
    p_advance.add_argument("--close-parent-status", default="done", choices=list(TASK_STATUSES))
    p_advance.add_argument("--next-input", default=None, help="If given, creates a new linked checkpoint with this raw_input")
    p_advance.add_argument("--next-action-type", default="follow_up", choices=list(ACTION_TYPES))
    p_advance.add_argument("--next-due-at", default=None, help="ISO 8601; only if Henry/the source actually gave this time")
    p_advance.add_argument("--next-check-at", default=None, help="ISO 8601; when the Pilot should next check on the new checkpoint")
    p_advance.add_argument("--next-waiting-on", default=None, choices=list(WAITING_ON_VALUES))
    p_advance.set_defaults(func=cmd_follow_up_advance)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
