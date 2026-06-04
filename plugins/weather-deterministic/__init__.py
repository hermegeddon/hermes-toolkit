"""weather-deterministic plugin entrypoint.

Wires two deterministic, no-LLM surfaces into Hermes:

1. ``pre_gateway_dispatch`` hook — fires on every inbound gateway MessageEvent
   BEFORE auth/agent dispatch. When the text looks like a weather request and
   is NOT already a slash command, it rewrites the message to
   ``/weather <maybe-location>``. The rewrite is then handled by the plugin
   ``/weather`` command (below) entirely within the gateway process — the
   AIAgent / LLM is never created for that turn.

2. ``/weather`` slash command (``register_command``) — available in gateway
   sessions AND the interactive CLI. ``handler(raw_args) -> str`` calls the
   deterministic Open-Meteo core and returns the formatted answer.

No model calls, no MCP, stdlib-only networking. See weather_core.py.
"""

from __future__ import annotations

import logging

from . import weather_core

logger = logging.getLogger("hermes_plugins.weather_deterministic")


def _weather_command_handler(raw_args: str):
    """Slash-command handler: /weather [<location>].

    Signature is ``fn(raw_args: str) -> str`` (sync), honored by both the
    gateway dispatch and the interactive CLI plugin-command path.
    """
    explicit = (raw_args or "").strip()
    # When invoked as a bare /weather with no args, explicit is "" and the core
    # falls back to the config-driven default location (see weather_core
    # get_default_location() / plugin.yaml config block).
    return weather_core.answer_weather(text=explicit, explicit_location=explicit)


def _pre_gateway_dispatch(event=None, gateway=None, session_store=None, agent_id=None, **_kw):
    """Rewrite plain-text weather requests to /weather, short-circuiting the LLM.

    Returns one of the documented pre_gateway_dispatch decisions:
      {"action": "rewrite", "text": "/weather <loc>"}  -> handled by /weather
      None                                             -> normal dispatch
    """
    try:
        text = getattr(event, "text", "") or ""
    except Exception:
        return None

    stripped = text.strip()
    if not stripped:
        return None

    # Never touch messages that are already slash commands (incl. an explicit
    # /weather — that path already reaches the deterministic handler).
    if stripped.startswith("/"):
        return None

    if not weather_core.is_weather_intent(stripped):
        return None

    location = weather_core.extract_location(stripped) or ""
    new_text = f"/weather {location}".strip()
    logger.debug("weather-deterministic: rewriting %r -> %r", stripped, new_text)
    return {"action": "rewrite", "text": new_text}


def register(ctx) -> None:
    """Plugin entrypoint called once by the PluginManager at load time."""
    ctx.register_command(
        name="weather",
        handler=_weather_command_handler,
        description="Deterministic weather (Open-Meteo, no LLM). /weather [location]",
        args_hint="<location>",
    )
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    logger.info("weather-deterministic plugin registered (/weather + pre_gateway_dispatch)")
