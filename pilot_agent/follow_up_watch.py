"""Follow-up Watch (2026-09-07, Follow-up Runtime Closure round).

This is the piece Henry's GPT-verified feedback said was actually missing:
a real, independent mechanism that watches next_check_at and pushes a
reminder when it's time -- as opposed to the fixed 08:15/22:00 Morning
Brief / Daily Close, which are summary layers only and must NOT be the
thing standing in for real follow-up tracking (Henry's explicit
instruction, item 6).

Designed to be invoked by Windows Task Scheduler once an hour
(`pilot_agent.main follow-up-watch`, see run-follow-up-watch.bat). Each
run is a fresh, stateless process -- all state that must survive between
runs (last_checked_at, reminder_count, next_check_at) lives in
data/pilot.db via the Interaction row itself, never in memory here.

Anti-spam design (Henry's explicit requirement: "不得重複無限狂發"):
  - Every time a row is picked up (next_check_at <= now), one reminder is
    sent, last_checked_at is set to now, and reminder_count += 1.
  - While reminder_count < MAX_AUTO_REMINDERS, next_check_at is pushed
    forward by RETRY_INTERVAL so an hourly job doesn't re-fire on the
    same row every single hour.
  - Once reminder_count reaches MAX_AUTO_REMINDERS, next_check_at is
    cleared to None -- the watch stops auto-firing on that row entirely.
    At that point it's no longer a "next_check_at" concern; it becomes a
    "waiting_on='henry'" concern (automated nagging didn't get an answer,
    so this now needs a human to actually look), which Daily Close's
    open-items snapshot will still surface (via open_without_due_date()),
    just without hourly noise.
  - If nothing is due, no message is sent at all -- silence is the
    correct, honest output for "nothing to check right now".
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Callable, List, Optional

from .models import Interaction

MAX_AUTO_REMINDERS = 3
RETRY_INTERVAL = dt.timedelta(hours=6)

SenderFn = Callable[[str], None]


@dataclasses.dataclass
class WatchOutcome:
    interaction_id: str
    raw_input_excerpt: str
    reminder_count_after: int
    backed_off: bool  # True if this firing hit MAX_AUTO_REMINDERS and next_check_at was cleared


def _reminder_message(interaction: Interaction) -> str:
    label = (interaction.raw_input or "").strip().replace("\n", " ")
    if len(label) > 200:
        label = label[:200] + "…"
    lines = [f"**[追蹤提醒]** {label}"]
    if interaction.due_at is not None:
        lines.append(f"（原定時間：{interaction.due_at.strftime('%Y-%m-%d %H:%M')}）")
    if interaction.waiting_on == "external":
        lines.append("目前狀態：等待外部回覆/事件發生中。")
    elif interaction.waiting_on == "henry":
        lines.append("目前狀態：需要你補充資訊才能繼續追蹤。")
    lines.append(f"（第 {interaction.reminder_count + 1} 次自動提醒）")
    return "\n".join(lines)


def run_follow_up_watch(
    repo,
    now: dt.datetime,
    *,
    send: Optional[SenderFn] = None,
) -> List[WatchOutcome]:
    """Core watch logic, independent of the CLI/Discord wiring so it can be
    unit-tested without a real Discord token. `send` defaults to
    notifications.send_message when None; tests should pass a recording
    stub instead.

    Returns the list of rows that were actually fired on this run (empty
    list, not None, when nothing was due -- callers should not send any
    "nothing to report" message; silence is correct here, unlike Daily
    Close which explicitly announces "nothing today")."""
    if send is None:
        from .notifications import send_message as send  # local import: avoid a hard Discord-token dependency for pure-logic tests

    due_rows = list(repo.due_for_check(now))
    outcomes: List[WatchOutcome] = []

    for interaction in due_rows:
        message = _reminder_message(interaction)
        send(message)

        interaction.last_checked_at = now
        interaction.reminder_count += 1

        backed_off = interaction.reminder_count >= MAX_AUTO_REMINDERS
        if backed_off:
            interaction.next_check_at = None
            # Automated nagging is exhausted with no resolution. Regardless
            # of what this was originally blocked on (external or henry),
            # a follow-up the Pilot gave up auto-chasing is now honestly a
            # "needs a human to look" situation -- overwrite waiting_on to
            # 'henry' so Daily Close's conditional send actually surfaces
            # it instead of silently treating it as still just "waiting on
            # some vendor" forever.
            interaction.waiting_on = "henry"
        else:
            interaction.next_check_at = now + RETRY_INTERVAL

        repo.save(interaction)

        excerpt = (interaction.raw_input or "").strip().replace("\n", " ")[:80]
        outcomes.append(
            WatchOutcome(
                interaction_id=interaction.id,
                raw_input_excerpt=excerpt,
                reminder_count_after=interaction.reminder_count,
                backed_off=backed_off,
            )
        )

    return outcomes
