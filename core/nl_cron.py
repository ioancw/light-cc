"""Natural-language → cron-expression parser for the ``/schedule`` wizard (W3).

The wizard's "when" step accepts free-text like *"every weekday at 9am"* or
*"every 2 hours"*. This module turns that into a 5-field cron expression plus
a human-readable echo so the wizard can confirm the interpretation back to
the user before persisting -- a drifting cron job is a silent failure, so
echo-and-confirm is the contract.

Power users can paste a raw 5-field cron expression; ``parse`` detects that
case and returns it unchanged with a humanized description so the same
contract holds. If the input is neither a recognised NL pattern nor a valid
cron expression, ``parse`` raises ``NlCronParseError`` with a specific message
naming the next thing the user could try.

Scope: deliberately thin. Cover the patterns most users actually want; tell
them clearly when something isn't supported instead of guessing wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from croniter import croniter


class NlCronParseError(ValueError):
    """Raised when natural-language input can't be parsed into a cron."""


@dataclass(frozen=True)
class ParseResult:
    cron: str
    human: str  # short description echoed back to the user


_WEEKDAY_NAMES = {
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wed": 3,
    "thursday": 4, "thu": 4, "thur": 4, "thurs": 4,
    "friday": 5, "fri": 5,
    "saturday": 6, "sat": 6,
    "sunday": 0, "sun": 0,
}

_WEEKDAY_LABELS = {
    0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
    4: "Thursday", 5: "Friday", 6: "Saturday",
}

# Match "9am", "9 am", "9:30am", "09:30", "9", "9pm", "noon", "midnight".
_TIME_RE = re.compile(
    r"""
    (?:
        (?P<hour>\d{1,2})            # hour (1-2 digits)
        (?::(?P<minute>\d{2}))?      # optional :MM
        \s*(?P<ampm>am|pm)?          # optional am/pm
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _parse_time(text: str) -> tuple[int, int] | None:
    """Parse 'HH', 'HH:MM', 'HHam', 'HHpm' into (hour, minute) or None.

    Also accepts 'noon' and 'midnight' as named constants. Returns ``None``
    when no recognisable time appears in ``text`` -- callers fall back to a
    sensible default (typically 09:00) rather than failing.
    """
    s = text.strip().lower()
    if not s:
        return None
    if s == "noon":
        return (12, 0)
    if s == "midnight":
        return (0, 0)

    m = _TIME_RE.fullmatch(s)
    if not m:
        return None

    hour = int(m.group("hour"))
    minute = int(m.group("minute") or 0)
    ampm = m.group("ampm")

    if ampm == "am":
        if hour == 12:
            hour = 0
        elif not (0 <= hour <= 12):
            return None
    elif ampm == "pm":
        if hour == 12:
            pass
        elif 1 <= hour <= 11:
            hour += 12
        else:
            return None
    else:
        # No am/pm marker -- treat as 24h. Reject obviously bad hours.
        if not (0 <= hour <= 23):
            return None

    if not (0 <= minute <= 59):
        return None

    return (hour, minute)


def _humanize_time(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _looks_like_cron(s: str) -> bool:
    """A 5-field cron expression croniter accepts."""
    parts = s.strip().split()
    if len(parts) != 5:
        return False
    return croniter.is_valid(s.strip())


def _humanize_cron(cron: str) -> str:
    """Best-effort short description of a raw cron string for echoing back.

    Recognises a handful of common shapes; falls back to ``cron``-as-text so
    the user still sees what was stored even if we don't have a friendly name.
    """
    parts = cron.strip().split()
    if len(parts) != 5:
        return cron
    minute, hour, dom, month, dow = parts

    # Specific common shapes -- order matters (most-specific first).
    if minute == "0" and hour == "9" and dom == "*" and month == "*" and dow == "1-5":
        return "every weekday at 09:00"
    if minute == "0" and hour == "9" and dom == "*" and month == "*" and dow == "*":
        return "every day at 09:00"
    if minute.startswith("*/") and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return f"every {minute[2:]} minutes"
    if minute == "0" and hour.startswith("*/") and dom == "*" and month == "*" and dow == "*":
        return f"every {hour[2:]} hours"
    if minute == "0" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "every hour on the hour"
    if (
        minute.isdigit() and hour.isdigit()
        and dom == "*" and month == "*" and dow == "*"
    ):
        return f"every day at {int(hour):02d}:{int(minute):02d}"
    if (
        minute.isdigit() and hour.isdigit() and dom == "*"
        and month == "*" and dow.isdigit()
    ):
        day = _WEEKDAY_LABELS.get(int(dow), f"weekday {dow}")
        return f"every {day} at {int(hour):02d}:{int(minute):02d}"
    if (
        minute.isdigit() and hour.isdigit() and dom == "1"
        and month == "*" and dow == "*"
    ):
        return f"first of every month at {int(hour):02d}:{int(minute):02d}"
    return f"cron `{cron}`"


def parse(text: str) -> ParseResult:
    """Parse natural-language schedule text into ``(cron, human)``.

    ``text`` is the user's raw answer to the wizard's "when" step. Recognises
    the patterns enumerated in the W3 plan (weekday/weekend, daily HH:MM,
    every N hours/minutes, every <weekday>, first of month). Raw cron
    expressions are passed through unchanged (after validation).

    Raises ``NlCronParseError`` with a specific suggestion when the input
    isn't recognised -- the wizard surfaces this back to the user verbatim,
    so the message has to point at a recovery path, not just say "no".
    """
    raw = text.strip()
    if not raw:
        raise NlCronParseError(
            "Please describe when this should run -- e.g. `every weekday at 9am`, "
            "`every Monday at 8:30`, or paste a 5-field cron like `0 9 * * 1-5`."
        )

    # ── Raw cron passthrough ──
    if _looks_like_cron(raw):
        return ParseResult(cron=raw, human=_humanize_cron(raw))

    s = raw.lower()
    s = re.sub(r"\s+", " ", s).strip()
    # Strip leading "at " sometimes prefixed by the user.
    if s.startswith("at "):
        s = s[3:].strip()

    # ── "every N minutes" ──
    m = re.fullmatch(r"every (\d{1,3}) ?min(?:ute)?s?", s)
    if m:
        n = int(m.group(1))
        if not (1 <= n <= 59):
            raise NlCronParseError(
                f"`every {n} minutes` isn't supported -- minute interval must be between 1 and 59."
            )
        return ParseResult(cron=f"*/{n} * * * *", human=f"every {n} minutes")

    # ── "every N hours" ──
    m = re.fullmatch(r"every (\d{1,2}) ?h(?:our|r)?s?", s)
    if m:
        n = int(m.group(1))
        if not (1 <= n <= 23):
            raise NlCronParseError(
                f"`every {n} hours` isn't supported -- hour interval must be between 1 and 23."
            )
        return ParseResult(cron=f"0 */{n} * * *", human=f"every {n} hours")

    # ── "every hour" / "hourly" ──
    if s in ("every hour", "hourly", "every hour on the hour"):
        return ParseResult(cron="0 * * * *", human="every hour on the hour")

    # ── "every weekday/weekend at <time>" ──
    m = re.fullmatch(r"every (weekday|weekdays|weekend|weekends)(?: at (.+))?", s)
    if m:
        which = m.group(1)
        time_str = (m.group(2) or "9am").strip()
        t = _parse_time(time_str)
        if t is None:
            raise NlCronParseError(
                f"Couldn't parse the time `{time_str}`. Try `9am`, `09:00`, or `9:30pm`."
            )
        h, mn = t
        dow = "1-5" if which.startswith("weekday") else "0,6"
        label = "weekday" if which.startswith("weekday") else "weekend"
        return ParseResult(
            cron=f"{mn} {h} * * {dow}",
            human=f"every {label} at {_humanize_time(h, mn)}",
        )

    # ── "every <weekday> at <time>" ──
    m = re.fullmatch(r"every (\w+)(?:s)?(?: (?:morning|afternoon|evening|night))?(?: at (.+))?", s)
    if m and m.group(1) in _WEEKDAY_NAMES:
        day_word = m.group(1)
        time_str = (m.group(2) or "").strip()
        # Allow "every monday morning" (no time) as 09:00 default.
        if not time_str:
            # Default times for time-of-day adjectives present in the input.
            if " morning" in s:
                t = (9, 0)
            elif " afternoon" in s:
                t = (14, 0)
            elif " evening" in s:
                t = (18, 0)
            elif " night" in s:
                t = (21, 0)
            else:
                t = (9, 0)
        else:
            parsed = _parse_time(time_str)
            if parsed is None:
                raise NlCronParseError(
                    f"Couldn't parse the time `{time_str}`. Try `9am`, `09:00`, or `9:30pm`."
                )
            t = parsed
        h, mn = t
        dow = _WEEKDAY_NAMES[day_word]
        return ParseResult(
            cron=f"{mn} {h} * * {dow}",
            human=f"every {_WEEKDAY_LABELS[dow]} at {_humanize_time(h, mn)}",
        )

    # ── "daily at <time>" / "every day at <time>" ──
    m = re.fullmatch(r"(?:daily|every day)(?: at (.+))?", s)
    if m:
        time_str = (m.group(1) or "9am").strip()
        t = _parse_time(time_str)
        if t is None:
            raise NlCronParseError(
                f"Couldn't parse the time `{time_str}`. Try `9am`, `09:00`, or `9:30pm`."
            )
        h, mn = t
        return ParseResult(
            cron=f"{mn} {h} * * *",
            human=f"every day at {_humanize_time(h, mn)}",
        )

    # ── "first of every month at <time>" / "first of the month" ──
    m = re.fullmatch(r"(?:first|1st) of (?:every|the) month(?: at (.+))?", s)
    if m:
        time_str = (m.group(1) or "9am").strip()
        t = _parse_time(time_str)
        if t is None:
            raise NlCronParseError(
                f"Couldn't parse the time `{time_str}`. Try `9am`, `09:00`, or `9:30pm`."
            )
        h, mn = t
        return ParseResult(
            cron=f"{mn} {h} 1 * *",
            human=f"first of every month at {_humanize_time(h, mn)}",
        )

    # ── No recognised pattern -- specific guidance, not generic "no". ──
    raise NlCronParseError(
        f"I can't parse `{raw}` yet. Try one of:\n"
        "- `every weekday at 9am`\n"
        "- `every Monday at 8:30`\n"
        "- `daily at 18:30`\n"
        "- `every 2 hours`\n"
        "- `every 30 minutes`\n"
        "- `first of every month at 9am`\n"
        "- or paste a 5-field cron expression like `0 9 * * 1-5`."
    )
