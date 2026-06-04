"""intent-handlers-core plugin entrypoint.

Wires three deterministic, no-LLM intents into Hermes using the SAME proven
pattern as the weather-deterministic plugin:

1. A single ``pre_gateway_dispatch`` hook — fires on every inbound gateway
   MessageEvent BEFORE auth/agent dispatch.  When the text matches one of the
   deterministic intents (and is NOT already a slash command), it rewrites the
   message to the corresponding namespaced slash command
   (``/time`` / ``/diskfree`` / ``/svcstatus``).  The rewrite is handled by the
   plugin slash command below, entirely within the gateway process — the
   AIAgent / LLM is never created for that turn.

2. Three slash commands (``register_command``) — available in gateway sessions
   AND the interactive CLI.  Each ``handler(raw_args) -> str | None`` calls the
   deterministic core.  When a command returns ``None`` (e.g. cluster-ops
   unconfigured/unreachable) the gateway falls back to normal handling.

STRICT FALL-THROUGH: every matcher/handler defers (no rewrite / None) on ANY
doubt.  No model calls, no MCP-via-agent; the only network is a single bounded
cluster-ops HTTP call for the disk / service-status intents.  See the *_core.py
modules.

Intents & determinism gate (all four conditions hold for each BUILT handler):
  - time/date  : pure stdlib, zero context, verifiable.            BUILD.
  - disk       : single cluster-ops disk_usage(host=hermes).       BUILD.
  - service    : cluster-ops service_status(host=hermes, unit=…),  BUILD.
                 unit parsed deterministically against an allow-list; any
                 ambiguous/unknown unit falls through.
"""

from __future__ import annotations

import logging

from . import disk_core, service_core, time_core

logger = logging.getLogger("hermes_plugins.intent_handlers_core")


# ── Slash command handlers (sync: fn(raw_args) -> str | None) ────────────────

def _time_command_handler(raw_args: str):
    """/time — current local time/date (America/Chicago). Always answers."""
    # raw_args may carry the original phrasing (date vs day vs time) when the
    # hook rewrites; pass it through so the reply is targeted.
    return time_core.answer_time(text=raw_args or "what time is it")


def _diskfree_command_handler(raw_args: str):
    """/diskfree — disk free/used for host hermes. None -> fall back to agent."""
    return disk_core.answer_disk(text=raw_args or "")


def _svcstatus_command_handler(raw_args: str):
    """/svcstatus <unit> — systemd status on hermes. None -> fall back."""
    args = (raw_args or "").strip()
    if not args:
        return None
    # Re-shape bare args into a question the parser understands, but ALSO accept
    # a question passed through verbatim by the hook.
    probe = args if service_core.is_service_intent(args) else f"is the {args} service running"
    return service_core.answer_service(probe)


# ── pre_gateway_dispatch hook ────────────────────────────────────────────────

def _pre_gateway_dispatch(event=None, gateway=None, session_store=None, agent_id=None, **_kw):
    """Rewrite plain-text deterministic intents to their slash command.

    Returns one of the documented pre_gateway_dispatch decisions:
      {"action": "rewrite", "text": "/time"}        -> handled by /time
      {"action": "rewrite", "text": "/diskfree"}    -> handled by /diskfree
      {"action": "rewrite", "text": "/svcstatus …"} -> handled by /svcstatus
      None                                          -> normal dispatch
    On ANY doubt: None.
    """
    try:
        text = getattr(event, "text", "") or ""
    except Exception:
        return None

    stripped = text.strip()
    if not stripped or stripped.startswith("/"):
        return None

    # Order matters only for disjoint matchers; these three are mutually
    # exclusive by construction, but we keep a stable priority anyway.
    try:
        if time_core.is_time_intent(stripped):
            logger.debug("intent-handlers-core: time %r -> /time", stripped)
            return {"action": "rewrite", "text": f"/time {stripped}".strip()}
        if disk_core.is_disk_intent(stripped):
            logger.debug("intent-handlers-core: disk %r -> /diskfree", stripped)
            return {"action": "rewrite", "text": "/diskfree"}
        if service_core.is_service_intent(stripped):
            logger.debug("intent-handlers-core: service %r -> /svcstatus", stripped)
            return {"action": "rewrite", "text": f"/svcstatus {stripped}".strip()}
    except Exception as exc:  # noqa: BLE001 — never break dispatch
        logger.debug("intent-handlers-core dispatch error (deferring): %s", exc)
        return None

    return None


def register(ctx) -> None:
    """Plugin entrypoint called once by the PluginManager at load time."""
    ctx.register_command(
        name="time",
        handler=_time_command_handler,
        description="Deterministic current time/date (America/Chicago, no LLM).",
    )
    ctx.register_command(
        name="diskfree",
        handler=_diskfree_command_handler,
        description="Deterministic disk free/used for host hermes (cluster-ops, no LLM).",
    )
    ctx.register_command(
        name="svcstatus",
        handler=_svcstatus_command_handler,
        description="Deterministic systemd service status on hermes (cluster-ops, no LLM).",
        args_hint="<unit>",
    )
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    logger.info(
        "intent-handlers-core registered (/time, /diskfree, /svcstatus + "
        "pre_gateway_dispatch)"
    )
