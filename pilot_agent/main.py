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
from .ids import generate_id
from .intake_classifier import classify
from .models import ACTION_TYPES, TASK_STATUSES, Interaction
from .notifications import DiscordPushError, send_message
from .providers.claude_provider import ClaudeProvider
from .storage import db as db_module
from .storage.sqlite_interaction_repository import SqliteInteractionRepository


def _repository() -> SqliteInteractionRepository:
    conn = db_module.connect(database_path())
    db_module.initialize_schema(conn)
    return SqliteInteractionRepository(conn)


def cmd_handle(args: argparse.Namespace) -> None:
    repo = _repository()
    model = ClaudeProvider()

    result = classify(model, args.input)

    due_at = dt.datetime.fromisoformat(result.due_at) if result.due_at else None

    interaction = Interaction(
        id=generate_id("interaction"),
        created_at=dt.datetime.now(dt.timezone.utc),
        source=args.source,
        channel_ref=args.channel_ref,
        raw_input=args.input,
        action_type=result.action_type,
        domain=result.domain,
        due_at=due_at,
        agent_response=result.response_text,
        model_used=result.model_used,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
        estimated_cost_usd=result.estimated_cost_usd,
        purpose="intake_classification",
        task_status="open" if result.action_type != "unknown" else "unknown",
    )
    repo.save(interaction)

    # Machine-readable line first (for programmatic callers), then the
    # plain response text a relaying session should actually send back.
    print(json.dumps({"id": interaction.id, "action_type": interaction.action_type, "domain": interaction.domain}))
    print("---")
    print(interaction.agent_response)


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


def _build_daily_close(repo, now: dt.datetime) -> str:
    """Functional Spec v1.0 SS5.3 simplified for Pilot v1: this repo has
    no 'Open Loop blocked on Henry' object type yet (that's a Foundation
    schema gap noted in the conflict-check report, not built here), so
    this reports a plain snapshot of what's still open at day's end,
    rather than the stricter "only send if something is stuck on Henry"
    behaviour the full spec describes. Says so explicitly rather than
    pretending to be the fuller SS5.3 behaviour."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    overdue_items = list(repo.overdue(now))
    no_date_open = [
        i for i in repo.open_without_due_date()
        if i.effective_action_type() in ("todo", "reminder", "follow_up")
    ]

    sections = [f"**22:00 今日總結** — {now.strftime('%Y-%m-%d (%a)')}"]

    if not overdue_items and not no_date_open:
        sections.append("今天沒有卡住的事項,也沒有逾期項目。")
    else:
        if overdue_items:
            sections.append(
                "**逾期未結**\n"
                + "\n".join(f"- (原訂 {i.due_at.strftime('%Y-%m-%d %H:%M')}) "
                             f"{(i.raw_input or '').strip()[:70]}" for i in overdue_items)
            )
        if no_date_open:
            sections.append(
                "**還沒排時間、仍待處理**\n"
                + "\n".join(_brief_line(i, show_due=False) for i in no_date_open)
            )

    sections.append(
        "（註：這是還開著的事項快照,不是規格書 SS5.3 原本設計的「只在真的卡住 Henry 時才發」"
        "那種 Open Loop 判斷 —— Pilot v1 還沒有 Open Loop 這個物件類型,先用簡化版。）"
    )
    return "\n\n".join(sections)


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
    message = _build_daily_close(repo, now)
    print(message)
    if args.no_send:
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

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
