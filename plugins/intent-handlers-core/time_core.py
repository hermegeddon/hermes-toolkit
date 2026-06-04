"""Deterministic time/date intent — pure Python, no LLM, no network.

Why: "what time is it" / "what's today's date" / "what day is it" are fully
determined by the system clock in a fixed timezone (America/Chicago). There is
zero user-specific context, no disambiguation, and the answer is verifiable
without an LLM. Per the determinism gate this is the canonical BUILD case.

What: A cheap synchronous matcher (``is_time_intent``) plus a formatter
(``answer_time``) that renders the current local time/date.  The matcher is
END-anchored and keyword-gated so conversational prose that merely contains the
word "time"/"date"/"day" ("tell me a story about time", "what would you do with
more time") does NOT match and falls through to the agent.

Test: ``is_time_intent("what time is it")`` is True; ``answer_time()`` contains
a weekday, an ISO date and a clock time; ``is_time_intent("a story about
time")`` is False.  See tests/test_time_core.py.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9 fallback (not expected here)
    ZoneInfo = None  # type: ignore[assignment]

TIMEZONE = "America/Chicago"

# Matcher.  Anchored so it fires on a *question about* the time/date, not on
# sentences that merely contain the words.  Accepted shapes (end-anchored,
# optional trailing "?"):
#   - "what time is it" / "what's the time" / "what is the time" (+ now/today/etc)
#   - "what's today's date" / "what is the date" / "what date is it"
#   - "what day is it" / "what day is it today" / "what's the day"
#   - "current time" / "current date" / "today's date" / "the time"
#   - bare "time" / "date" is intentionally NOT matched (too ambiguous —
#     "time" could be a noun in countless prose contexts).
_TIME_RE = re.compile(
    r"^\s*"
    r"(?:"
    # what time is it / what's the time / what is the current time
    r"what(?:'|’)?s?(?:\s+is)?\s+(?:the\s+|current\s+)?time(?:\s+is\s+it)?"
    r"|what\s+time\s+is\s+it"
    r"|current\s+time|the\s+time"
    r")"
    r"(?:\s+(?:now|right\s+now|today|currently|here|please))?"
    r"\s*\??\s*$",
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r"^\s*"
    r"(?:"
    # what's today's date / what is the date / what date is it
    r"what(?:'|’)?s?(?:\s+is)?\s+(?:the\s+|today(?:'|’)?s?\s+|current\s+)?date(?:\s+is\s+it)?"
    r"|what\s+date\s+is\s+it"
    r"|what(?:'|’)?s?\s+today(?:'|’)?s?\s+date"
    r"|current\s+date|today(?:'|’)?s?\s+date|the\s+date"
    r")"
    r"(?:\s+(?:now|right\s+now|today|currently|please))?"
    r"\s*\??\s*$",
    re.IGNORECASE,
)

_DAY_RE = re.compile(
    r"^\s*"
    r"(?:"
    # what day is it / what's the day / what day is it today
    r"what\s+day\s+is\s+it(?:\s+today)?"
    r"|what(?:'|’)?s?(?:\s+is)?\s+(?:the\s+)?day(?:\s+today)?"
    r"|what\s+day\s+of\s+the\s+week\s+is\s+it"
    r")"
    r"(?:\s+(?:now|right\s+now|today|currently|please))?"
    r"\s*\??\s*$",
    re.IGNORECASE,
)


def is_time_intent(text: str) -> bool:
    """Return True iff ``text`` is a time/date/day question.

    Why: Gate the handler so we never short-circuit unrelated prose.
    What: Tries the time, date and day regexes (all end-anchored).
    Test: True for "what time is it", "what's today's date", "what day is it";
    False for "tell me a story about time", "schedule a meeting", "/time".
    """
    if not text or not text.strip():
        return False
    s = text.strip()
    return bool(_TIME_RE.match(s) or _DATE_RE.match(s) or _DAY_RE.match(s))


def _kind(text: str) -> str:
    """Classify a matched time/date/day question (for terse, targeted output)."""
    s = (text or "").strip()
    if _DAY_RE.match(s):
        return "day"
    if _DATE_RE.match(s):
        return "date"
    return "time"


def _now() -> datetime:
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo(TIMEZONE))
    return datetime.now()  # pragma: no cover


def answer_time(text: Optional[str] = None) -> str:
    """Render the current local time/date as a terse, messaging-safe reply.

    Why: This is the latency win — a stdlib answer instead of an agent loop.
    What: Formats ``datetime.now`` in America/Chicago.  When ``text`` narrows
    the request to just the date or just the day-of-week, the reply leads with
    that; otherwise it gives the full clock time.  Always self-contained.
    Test: ``answer_time("what time is it")`` contains a ":" clock and "CDT"/
    "CST"; ``answer_time("what day is it")`` contains a weekday name.
    """
    now = _now()
    weekday = now.strftime("%A")
    iso_date = now.strftime("%Y-%m-%d")
    pretty_date = now.strftime("%B %-d, %Y") if hasattr(now, "strftime") else iso_date
    clock = now.strftime("%-I:%M %p").lstrip("0")
    tzname = now.strftime("%Z") or TIMEZONE
    kind = _kind(text or "")

    if kind == "day":
        return f"Today is {weekday} ({iso_date})."
    if kind == "date":
        return f"Today is {weekday}, {pretty_date} ({iso_date})."
    # time (default): give clock + date so the answer is fully self-contained.
    return f"It is {clock} {tzname} on {weekday}, {pretty_date}."
