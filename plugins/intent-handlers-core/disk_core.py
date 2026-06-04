"""Deterministic disk-free intent — cluster-ops disk_usage on host ``hermes``.

Why: "how much disk is free" / "disk space" / "disk usage" is answerable from a
single deterministic cluster-ops ``disk_usage`` call on the fixed host
``hermes``.  Zero user-specific context, no disambiguation, output fully
determined by the live filesystem, verifiable without an LLM.  Per the
determinism gate: BUILD.

What: ``is_disk_intent`` is an end-anchored keyword matcher; ``answer_disk``
calls cluster-ops and renders free/used GB per filesystem.  Strict
fall-through: if cluster-ops is unreachable / unconfigured / returns no
filesystems, ``answer_disk`` returns ``None`` and the request defers to the
agent.

Test: ``is_disk_intent("how much disk is free")`` True; ``is_disk_intent("what
should I do about my busy schedule")`` False.  ``answer_disk`` with a canned
payload renders "free"/"GB"; with cluster-ops unconfigured -> None.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

try:
    from . import cluster_ops_client
except ImportError:  # standalone import (e.g. unit tests on the plugin dir path)
    import cluster_ops_client  # type: ignore[no-redef]

logger = logging.getLogger("hermes_plugins.intent_handlers_core.disk")

# Fixed host — this gateway runs on CT 133 "hermes".  The disk intent is only
# ever about *this* machine's disk; a question naming another host is prose we
# defer to the agent (the matcher won't fire on it).
_HOST = "hermes"

# Matcher.  End-anchored, keyword-gated.  Requires the word "disk" (or the
# idiomatic "free space" / "storage space") so generic "space"/"usage" prose
# does not match.  Rejects sentences that merely contain "disk" in a non-query
# context by anchoring to the question shapes below.
_DISK_RE = re.compile(
    r"^\s*"
    r"(?:"
    # how much disk (space) is free / left ; how much free disk (space)
    r"how\s+much\s+(?:disk|free\s+disk)\s*(?:space|storage)?\s+(?:is\s+)?(?:free|left|available|used)"
    r"|how\s+much\s+(?:disk\s+)?space\s+(?:is\s+)?(?:free|left|available)\s+on\s+disk"
    # disk space / disk usage / disk free / free disk space / disk space left
    r"|(?:free\s+)?disk\s*(?:space|usage|free|usage\s+report)?(?:\s+(?:left|free|available|used|report))?"
    r"|disk\s+free"
    # what's the disk usage / show disk usage / check disk space
    r"|(?:what(?:'|’)?s?\s+(?:the\s+)?|show\s+(?:me\s+)?(?:the\s+)?|check\s+(?:the\s+)?)disk\s*(?:space|usage|free)"
    r")"
    r"(?:\s+(?:on\s+hermes|here|now|please|today))?"
    r"\s*\??\s*$",
    re.IGNORECASE,
)


def is_disk_intent(text: str) -> bool:
    """Return True iff ``text`` is a disk-space question about this host.

    Why: Gate the handler so only clear disk questions short-circuit the agent.
    What: End-anchored keyword match requiring "disk" (or "free disk space").
    Test: True for "how much disk is free", "disk space", "disk usage", "what's
    the disk usage"; False for "tell me about disk drives in history",
    "how much time do I have", "/disk".
    """
    if not text or not text.strip():
        return False
    return bool(_DISK_RE.match(text.strip()))


def _fmt_gb(value) -> Optional[float]:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def answer_disk(text: Optional[str] = None) -> Optional[str]:
    """Answer a disk-space question from cluster-ops, or None to fall through.

    Why: deterministic HTTP answer instead of an agent loop.
    What: calls cluster-ops ``disk_usage`` for host ``hermes`` and renders
    free/used GB per mount.  Returns ``None`` on any failure (unconfigured
    creds, transport/HTTP error, empty/malformed payload) — strict fall-through.
    Test: canned payload -> string containing "free" and "GB"; cluster-ops
    unconfigured -> None.
    """
    data = cluster_ops_client.call_tool("disk_usage", {"host": _HOST})
    if not isinstance(data, dict):
        return None
    filesystems = data.get("filesystems")
    if not isinstance(filesystems, list) or not filesystems:
        return None

    lines = [f"*Disk usage — {data.get('host', _HOST)}*"]
    rendered = 0
    for fs in filesystems:
        if not isinstance(fs, dict):
            continue
        mount = fs.get("mount") or "?"
        total = _fmt_gb(fs.get("total_gb"))
        used = _fmt_gb(fs.get("used_gb"))
        if total is None or used is None:
            continue
        free = round(total - used, 1)
        pct = fs.get("use_pct")
        try:
            pct_str = f"{round(float(pct))}% used" if pct is not None else ""
        except (TypeError, ValueError):
            pct_str = ""
        line = f"{mount}: {free} GB free of {total} GB"
        if pct_str:
            line += f" ({pct_str})"
        lines.append(line)
        rendered += 1

    if rendered == 0:
        # No usable filesystem rows — don't emit a header-only answer.
        return None
    return "\n".join(lines)
