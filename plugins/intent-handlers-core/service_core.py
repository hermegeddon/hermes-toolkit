"""Deterministic service-status intent — cluster-ops service_status on ``hermes``.

Why: "is <svc> running" / "is <svc> up" / "status of <svc>" is answerable from a
single deterministic cluster-ops ``service_status`` call on host ``hermes`` —
PROVIDED the service name parses cleanly to a known systemd unit.  Output fully
determined by systemd state, no LLM needed.  Per the determinism gate: BUILD,
but with a hard parse gate — if the service name is ambiguous or unparseable we
return ``None`` and fall through to the agent.

What: ``is_service_intent`` matches the question shape AND extracts a candidate
unit name; ``parse_unit`` normalizes/validates it against an allow-list of known
units (so we never query an arbitrary attacker-supplied unit and never guess);
``answer_service`` calls cluster-ops and renders active/sub-state.  Any doubt ->
``None``.

Test: "is the hermes-gateway service running" -> matches, unit "hermes-gateway";
"is it raining" -> no match; "is my code any good" -> no match; "is foobar
running" -> matches shape but parse_unit returns None (unknown unit) ->
fall-through.  See tests/test_service_core.py.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

try:
    from . import cluster_ops_client
except ImportError:  # standalone import (e.g. unit tests on the plugin dir path)
    import cluster_ops_client  # type: ignore[no-redef]

logger = logging.getLogger("hermes_plugins.intent_handlers_core.service")

_HOST = "hermes"

# Allow-list of systemd units we will deterministically answer for.  Mapping
# from a normalized alias -> canonical unit name.  A question naming anything
# NOT in this map falls through to the agent (no guessing, no arbitrary unit
# query).  Keep aliases lowercase and hyphen/space-insensitive (we normalize
# the candidate the same way before lookup).
_UNIT_ALIASES = {
    "hermes-gateway": "hermes-gateway",
    "hermesgateway": "hermes-gateway",
    "hermes gateway": "hermes-gateway",
    "gateway": "hermes-gateway",
    "hermes": "hermes-gateway",
    "the gateway": "hermes-gateway",
    "hermes-agent": "hermes-gateway",
}

# Stopwords that, if they ARE the captured service name, mean the sentence was
# weather/prose ("is it raining", "is it cold") rather than a service question.
_NON_SERVICE_WORDS = {
    "it", "this", "that", "everything", "anything", "something",
    "raining", "snowing", "sunny", "cold", "hot", "warm", "cool",
    "ok", "okay", "good", "fine", "alright", "ready", "done", "there",
    "he", "she", "they", "the", "a", "an", "my", "your",
}

# Matcher.  Three question shapes, each with a captured <svc> group.  The
# <svc> capture is intentionally narrow: a short run of word/.-_ tokens, so we
# don't swallow a whole prose clause.  ``service``/``daemon``/``unit`` are
# optional trailing nouns we strip during normalization.
_RUNNING_RE = re.compile(
    r"^\s*is\s+(?:the\s+)?(?P<svc>[\w][\w .\-]{0,40}?)"
    r"(?:\s+(?:service|daemon|unit))?\s+"
    r"(?:running|up|active|alive|online|down|stopped|dead|ok)"
    r"\s*\??\s*$",
    re.IGNORECASE,
)
_STATUS_OF_RE = re.compile(
    r"^\s*(?:what(?:'|’)?s?\s+the\s+|check\s+the\s+|show\s+(?:me\s+)?the\s+)?"
    r"status\s+of\s+(?:the\s+)?(?P<svc>[\w][\w .\-]{0,40}?)"
    r"(?:\s+(?:service|daemon|unit))?"
    r"\s*\??\s*$",
    re.IGNORECASE,
)
_IS_X_RUNNING_RE = re.compile(
    r"^\s*is\s+(?:the\s+)?(?P<svc>[\w][\w .\-]{0,40}?)\s+(?:service|daemon|unit)\s+"
    r"(?:running|up|active|alive|online|down|stopped|dead|ok)?"
    r"\s*\??\s*$",
    re.IGNORECASE,
)


def _capture(text: str) -> Optional[str]:
    """Return the raw captured service phrase from a matching shape, or None."""
    s = (text or "").strip()
    for rx in (_RUNNING_RE, _IS_X_RUNNING_RE, _STATUS_OF_RE):
        m = rx.match(s)
        if m:
            cand = (m.group("svc") or "").strip()
            if cand:
                return cand
    return None


def _normalize(candidate: str) -> str:
    """Lowercase, strip trailing service/daemon/unit nouns, collapse spaces."""
    cand = candidate.strip().lower()
    cand = re.sub(r"\b(service|daemon|unit)\b", " ", cand)
    cand = re.sub(r"[\s]+", " ", cand).strip()
    return cand


def parse_unit(text: str) -> Optional[str]:
    """Deterministically resolve a question to a canonical systemd unit, or None.

    Why: We must never query an arbitrary/unknown unit or guess.  Only questions
    that resolve to an allow-listed unit get a fast-path answer; everything else
    falls through to the agent.
    What: extracts the candidate phrase, rejects prose stopwords ("it", "raining"
    …), normalizes (strip service/daemon noun), and looks it up in
    ``_UNIT_ALIASES``.  Tries the hyphen-collapsed form too.
    Test: "is the hermes-gateway service running" -> "hermes-gateway";
    "is the gateway up" -> "hermes-gateway"; "is foobar running" -> None;
    "is it raining" -> None.
    """
    cand = _capture(text)
    if not cand:
        return None
    norm = _normalize(cand)
    if not norm or norm in _NON_SERVICE_WORDS:
        return None
    # Direct alias hit (space form).
    if norm in _UNIT_ALIASES:
        return _UNIT_ALIASES[norm]
    # Hyphen-collapsed form ("hermes gateway" -> "hermes-gateway").
    hyphen = norm.replace(" ", "-")
    if hyphen in _UNIT_ALIASES:
        return _UNIT_ALIASES[hyphen]
    nospace = norm.replace(" ", "")
    if nospace in _UNIT_ALIASES:
        return _UNIT_ALIASES[nospace]
    # Unknown unit — do NOT guess. Fall through to the agent.
    return None


def is_service_intent(text: str) -> bool:
    """Return True iff ``text`` is a service-status question for a KNOWN unit.

    Why: Gate the handler — we only short-circuit when the unit parses cleanly.
    What: True iff a question shape matches AND ``parse_unit`` resolves it to an
    allow-listed unit.  An unparseable/unknown unit returns False (fall-through).
    Test: True for "is the hermes-gateway service running", "is the gateway up",
    "status of hermes-gateway"; False for "is it raining", "is foobar running",
    "what services do you offer", "/svcstatus".
    """
    if not text or not text.strip():
        return False
    return parse_unit(text) is not None


def _humanize_state(active: str, sub: str) -> str:
    active = (active or "").lower()
    sub = (sub or "").lower()
    if active == "active" and sub == "running":
        return "running"
    if active == "active":
        return f"active ({sub})" if sub else "active"
    if active == "inactive":
        return "stopped"
    if active == "failed":
        return "FAILED"
    return f"{active or 'unknown'}" + (f" ({sub})" if sub else "")


def answer_service(text: str) -> Optional[str]:
    """Answer a service-status question from cluster-ops, or None to fall through.

    Why: deterministic systemd answer instead of an agent loop.
    What: resolves the unit (parse_unit), calls cluster-ops ``service_status``
    for host ``hermes``, and renders active/sub-state + pid + uptime.  Returns
    ``None`` if the unit doesn't resolve or cluster-ops fails/returns no state.
    Test: unknown unit -> None; cluster-ops unconfigured -> None; canned active
    payload -> string containing the unit name and "running".
    """
    unit = parse_unit(text)
    if not unit:
        return None
    data = cluster_ops_client.call_tool(
        "service_status", {"host": _HOST, "unit": unit}
    )
    if not isinstance(data, dict):
        return None
    active = data.get("active")
    sub = data.get("sub_state")
    if active is None and sub is None and data.get("loaded") is None:
        return None  # no usable state — defer

    state = _humanize_state(active or "", sub or "")
    lines = [f"*{unit}* on hermes: {state}"]

    since = data.get("since")
    if isinstance(since, str) and since.strip():
        lines.append(f"since {since.strip()}")
    pid = data.get("pid")
    if isinstance(pid, int) and pid > 0:
        lines.append(f"pid {pid}")
    restarts = data.get("restarts")
    if isinstance(restarts, int):
        lines.append(f"{restarts} restart(s)")

    return "\n".join(lines) if len(lines) == 1 else lines[0] + " — " + ", ".join(lines[1:])
