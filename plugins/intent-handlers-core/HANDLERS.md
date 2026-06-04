# Deterministic Intent Handlers — Registry

Pre-LLM intent handlers that answer fully-deterministic questions in the gateway
process **without creating an AIAgent or making any LLM/MCP-via-agent call**.

## Deployment mechanism (the clean one)

These handlers are a **Hermes user plugin** (`kind: standalone`), NOT a core edit
to `intent_fast_path.py`. They live outside the `hermes-agent` repo:

- **Source of truth (git):** `/opt/hermes/toolkit/plugins/intent-handlers-core/`
  (committed to the toolkit repo, branch `main`).
- **Deployed copy:** `~/.hermes/plugins/intent-handlers-core/` (loaded by the
  PluginManager at gateway/CLI startup; enabled via `plugins.enabled`).

Each intent uses the same proven pattern as the `weather-deterministic` plugin:

1. A single `pre_gateway_dispatch` plugin hook fires on every inbound gateway
   `MessageEvent` (before auth/agent dispatch). On a clean intent match it
   returns `{"action": "rewrite", "text": "/<command> ..."}`.
2. The rewritten message resolves to a plugin **slash command**
   (`/time`, `/diskfree`, `/svcstatus`) whose handler returns the answer string.
   The gateway returns that string and never reaches `_run_agent` /
   `run_conversation`. Zero LLM, zero agent.

On a handler returning `None` (or the matcher returning `False`), the message
falls through to the normal agent pipeline. **Strict fall-through on ANY doubt.**

## Determinism gate

A handler is built ONLY if all four hold:
(a) zero user-specific context, (b) output fully determined by public/local data,
(c) no disambiguation needed, (d) verifiable without an LLM.

| Intent | Slash cmd | Trigger patterns (end-anchored, keyword-gated) | Data source | Gate (a/b/c/d) | Status |
|---|---|---|---|---|---|
| **time/date** | `/time` | "what time is it", "what's the time", "current time", "what's today's date", "what date is it", "what day is it", "what day of the week is it" (+ now/today/right now fillers) | Pure Python `datetime` in `zoneinfo` **America/Chicago** (no network) | ✅/✅/✅/✅ | **BUILT** |
| **disk** | `/diskfree` | "how much disk is free/left", "disk space", "disk usage", "disk free", "free disk space", "what's the disk usage", "show/check disk space" | cluster-ops MCP `disk_usage(host="hermes")` — single bounded HTTP call | ✅/✅/✅/✅ | **BUILT** |
| **service-status** | `/svcstatus` | "is &lt;svc&gt; running/up/active", "status of &lt;svc&gt;" — **only** when `<svc>` resolves to an allow-listed unit | cluster-ops MCP `service_status(host="hermes", unit=...)` | ✅/✅/✅(parse gate)/✅ | **BUILT** |

### Matcher false-positive guards (must FALL THROUGH)

- time: "tell me a story about time", "how much time do I have", "save the date",
  bare "time"/"date" (too ambiguous).
- disk: "tell me about disk drives in history", "the floppy disk era".
- service: "is it raining" (weather — handled by the weather plugin),
  "is my code any good", "what services do you offer", "is everything ok",
  and any **unknown unit** ("is redis running", "is nginx up") — no guessing.

### service-status unit allow-list

`parse_unit()` only resolves to a known systemd unit; anything else falls through.
Current aliases → canonical unit:

| Alias(es) | Unit |
|---|---|
| `hermes-gateway`, `hermes gateway`, `gateway`, `the gateway`, `hermes`, `hermes-agent` | `hermes-gateway` |

Extend `_UNIT_ALIASES` in `service_core.py` to add units (keep it an allow-list).

## Config / env keys

| Key | Where | Purpose |
|---|---|---|
| `plugins.enabled: [... intent-handlers-core]` | **both** `config.yaml` (gateway `/opt/hermes/home/config.yaml` + CLI `/opt/hermes/home/.hermes/config.yaml`) | Opt-in load of the plugin |
| `CLUSTER_OPS_URL` | `~/.hermes/.env` | cluster-ops MCP streamable-HTTP endpoint (disk/service) |
| `CLUSTER_OPS_TOKEN` | `~/.hermes/.env` (secret) | Bearer token for cluster-ops |

Without the two `CLUSTER_OPS_*` keys the **disk** and **service-status** handlers
fall through to the agent; **time/date** still works (no network).

## Bounds & safety

- cluster-ops calls: per-request socket timeout 4s, hard total wall-clock ceiling
  6s (`cluster_ops_client._TOTAL_CEILING_S`). Any failure → `None` → fall through.
- No handler ever raises into the gateway; `pre_gateway_dispatch` swallows all
  exceptions and defers.
- The token is read from the environment only — never hardcoded in the plugin.

## Tests

`./run_tests.sh` (21 unit tests): matchers (true + false-positive sets),
formatters, fall-through on unconfigured/empty cluster-ops, and the
service-status parse gate (unknown unit → None). cluster-ops is monkeypatched so
the unit suite needs no network.
