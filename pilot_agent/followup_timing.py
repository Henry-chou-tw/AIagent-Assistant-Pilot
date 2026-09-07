"""Deterministic same-day/next-day follow-up scheduling policy (2026-09-07,
Temporal Follow-up Reasoning Correction round -- GPT's independent
verification flagged the 克靈固 case as FAIL).

Root cause of the FAIL: the previous round let the classifier model output
a raw next_check_at datetime directly, and the system prompt's own worked
example told it to pick "明天上午" (tomorrow morning) for a message that
actually said "稍晚補充" (I'll tell you later TODAY). That's a same-day
pending-information-promise, not a next-day one -- the Pilot should have
gone back today, not waited until tomorrow to ask for the first time.

The fix is NOT "let the model compute a better datetime". Free-form model
datetime arithmetic is exactly what produced the wrong answer, and it's
untestable without a live model call. Instead: the classifier only ever
has to make a *qualitative* judgement -- does this message imply
same-day, next-day, or further-out pending information? -- via
next_check_hint (see claude_provider.py's system prompt section 2b). This
module turns that qualitative hint into a concrete datetime, entirely in
code, so the actual scheduling math is deterministic and unit-testable
without ever invoking a model.

This must never be confused with due_at: due_at is a fact about the world
(when the vendor will actually deliver) and is never touched here. This
module only ever produces next_check_at values -- the Pilot's own
scheduling decision about when to go ask again.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

NEXT_CHECK_HINTS = ("same_day", "next_day", "later")

# How soon to check back today when a message implies "I'll tell you
# later today" (稍後/晚點/待會/稍晚 etc.). Not precise, not a claim about
# when the vendor/colleague will actually respond -- just a reasonable
# operational polling cadence.
SAME_DAY_INTERVAL_HOURS = 2.5

# Don't schedule a same-day check-in this late -- clamp to this hour
# instead of pushing further into the evening.
SAME_DAY_CUTOFF_HOUR = 21

# If "now" itself is already at/after the cutoff (e.g. the message arrived
# at 22:00), there's no normal same-day slot left before the cutoff --
# fall back to a short interval instead, but NEVER cross into tomorrow's
# calendar date; see the end-of-day clamp below.
SAME_DAY_LATE_FALLBACK_HOURS = 1

# Fixed check-in hour the next calendar day, for an explicit "tomorrow"
# pending-information promise (明天再回覆/明天說/明天確認).
NEXT_DAY_CHECK_HOUR = 9


def compute_next_check_at(hint: Optional[str], now: dt.datetime) -> Optional[dt.datetime]:
    """Pure, deterministic: same (hint, now) always produces the same
    result -- no randomness, no live clock reads beyond the `now` passed
    in. `now` must be timezone-aware (same discipline as the rest of the
    Pilot's datetime handling).

    - "same_day": now + SAME_DAY_INTERVAL_HOURS, clamped so it never
      falls after SAME_DAY_CUTOFF_HOUR and never rolls into tomorrow's
      calendar date even in a late-night edge case.
    - "next_day": tomorrow at NEXT_DAY_CHECK_HOUR:00.
    - "later" (a further-out vague promise like 下週/改天) or None or any
      unrecognized value: returns None -- deliberately never guessed.
      A "later" item surfaces instead via Daily Close's "尚未排時間"
      bucket, not via a fabricated auto-schedule.
    """
    if hint == "same_day":
        end_of_today = now.replace(hour=23, minute=59, second=0, microsecond=0)
        cutoff = now.replace(hour=SAME_DAY_CUTOFF_HOUR, minute=0, second=0, microsecond=0)
        if now >= cutoff:
            candidate = now + dt.timedelta(hours=SAME_DAY_LATE_FALLBACK_HOURS)
            return min(candidate, end_of_today)
        candidate = now + dt.timedelta(hours=SAME_DAY_INTERVAL_HOURS)
        return min(candidate, cutoff)

    if hint == "next_day":
        tomorrow = now + dt.timedelta(days=1)
        return tomorrow.replace(hour=NEXT_DAY_CHECK_HOUR, minute=0, second=0, microsecond=0)

    return None
