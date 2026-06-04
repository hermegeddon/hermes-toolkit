"""Unit tests for the time/date deterministic handler (matcher + formatter).

Run: python -m pytest tests/test_time_core.py  (from the plugin dir, or via the
run_tests.sh harness which sets the import path).
"""

import time_core


def test_matches_time_questions():
    for q in [
        "what time is it",
        "what time is it?",
        "what's the time",
        "what is the time",
        "what's the time now",
        "current time",
        "the time please",
        "what time is it right now",
    ]:
        assert time_core.is_time_intent(q) is True, q


def test_matches_date_questions():
    for q in [
        "what's today's date",
        "what is the date",
        "what date is it",
        "today's date",
        "current date",
        "what's the date?",
    ]:
        assert time_core.is_time_intent(q) is True, q


def test_matches_day_questions():
    for q in [
        "what day is it",
        "what day is it today",
        "what's the day",
        "what day of the week is it",
    ]:
        assert time_core.is_time_intent(q) is True, q


def test_false_positives_fall_through():
    # MUST NOT match — prose / unrelated, defers to the agent.
    for q in [
        "tell me a story about time",
        "what would you do with more time",
        "how much time do I have until the meeting",
        "time flies when you're having fun",
        "schedule a meeting for tomorrow",
        "what is the meaning of time",
        "set a timer",
        "date night ideas",
        "save the date for the party",
        "time",         # bare noun — intentionally ambiguous
        "date",         # bare noun
        "/time",        # slash command, never matched as prose
        "",
        "   ",
    ]:
        assert time_core.is_time_intent(q) is False, q


def test_answer_time_is_self_contained():
    out = time_core.answer_time("what time is it")
    assert ":" in out            # clock present
    assert any(c.isdigit() for c in out)
    # timezone abbrev present (CDT or CST depending on DST)
    assert ("CDT" in out) or ("CST" in out) or ("America/Chicago" in out)


def test_answer_day_has_weekday():
    out = time_core.answer_time("what day is it")
    assert any(
        d in out
        for d in ("Monday", "Tuesday", "Wednesday", "Thursday",
                  "Friday", "Saturday", "Sunday")
    )


def test_answer_date_has_iso():
    out = time_core.answer_time("what's today's date")
    import re
    assert re.search(r"\d{4}-\d{2}-\d{2}", out)
