---
name: hermes-internals
description: >-
  Authoritative map of how Hermes Agent (NousResearch) works under the hood: the
  four ways to drive it (CLI, the AIAgent Python library, the OpenAI-compatible
  API server, and the messaging gateway), which surface to use for which job, the
  AIAgent constructor knobs that control speed/cost, and the gateway's message
  flow, session keys, authorization, hooks, and delivery. Use this skill WHENEVER
  working on, testing, debugging, evaluating, or extending a Hermes instance,
  gateway, or skill — especially to decide "should I use the CLI, the library, the
  API, or the gateway for this?" and to understand why CLI-per-call testing is slow.
---

# Hermes Agent — Internals & Surfaces

Hermes is a self-improving, terminal-native agent: persistent memory, agent-created
skills, a 20+ platform messaging gateway, runnable on local/Docker/SSH/Daytona/
Modal/Singularity backends, against Nous Portal / OpenRouter / OpenAI / Anthropic /
any OpenAI-compatible endpoint. MIT licensed. Repo:
`github.com/NousResearch/hermes-agent`. Docs: `hermes-agent.nousresearch.com/docs`.

A machine-readable doc index lives at `/docs/llms.txt`; the full concatenated docs
at `/docs/llms-full.txt`.

## The four surfaces (and when to use each)

Hermes is one engine you can reach four ways. Choosing the right one is the single
biggest lever on speed when testing or automating.

| Surface | What it is | Best for | Avoid for |
|---|---|---|---|
| **CLI** (`hermes chat -q "…"`) | one headless run per process | a human running one ad-hoc prompt | batch/QA — cold start per call is the bottleneck |
| **Python library** (`from run_agent import AIAgent`) | in-process agent, full control | QA at scale, batch trajectories, embedding in tools, tool-call inspection | nothing — this is the default for automation |
| **API server** (`api_server` adapter / `hermes serve`) | OpenAI-compatible `/v1/chat/completions` | end-to-end tests through the real serving path, any OpenAI-style frontend | inspecting intermediate tool calls (returns final text only) |
| **Gateway** (`hermes gateway start`) | long-lived multi-platform message router | production: Telegram/Discord/Slack/…, cron delivery, pairing | unit-style testing of agent logic (too much machinery) |

**Speed rule of thumb:** anything that runs more than one prompt should use the
library (in-process + parallel) or the API server, never a loop of `hermes chat`.

## AIAgent — the knobs that matter

```python
from run_agent import AIAgent

agent = AIAgent(
    model="anthropic/claude-sonnet-4.6",
    quiet_mode=True,          # suppress CLI spinners — REQUIRED when embedding
    skip_memory=True,         # don't read/write MEMORY.md — stateless & faster
    skip_context_files=True,  # don't inject AGENTS.md/.hermes.md
    max_iterations=6,         # cap tool-calling loops (default 90 is generous)
    enabled_toolsets=["web"], # or disabled_toolsets=[...]
)
text = agent.chat("…")                     # -> final string
result = agent.run_conversation("…")       # -> {"final_response", "messages"}
```

| Param | Why you'd set it |
|---|---|
| `quiet_mode=True` | clean output; mandatory for programmatic use |
| `skip_memory=True` | stateless QA / API endpoints; avoids memory read+flush cost |
| `skip_context_files=True` | reproducible prompts independent of cwd context files |
| `max_iterations` | the default 90 lets simple prompts spend on needless tool loops; 4–10 is plenty for QA |
| `enabled_toolsets` / `disabled_toolsets` | lock the agent to (or away from) `web`, `terminal`, `browser`, etc. |
| `ephemeral_system_prompt` | specialize behavior without polluting saved trajectories |
| `save_trajectories=True` | append ShareGPT-format JSONL for datasets/debugging |
| `base_url` / `api_key` | point at a specific provider/endpoint |
| `platform="discord"` | inject platform formatting hints |

**Parallelism:** create **one fresh `AIAgent` per thread/task** (internal state is
not thread-safe), then fan out with `concurrent.futures.ThreadPoolExecutor`. For
turnkey batch runs Hermes ships `batch_runner.py --input prompts.jsonl --output
results.jsonl` (per-task isolation + checkpointing). The bundled `hermes-eval-harness`
skill wraps all of this for QA.

## Skill vs Tool (when extending capability)

- **Skill** = instructions + shell + existing tools, no code changes to the agent.
  Wraps a CLI/API the agent calls via `terminal`/`web_extract`. Most extensions.
- **Tool** = needs baked-in API keys/auth, precise custom logic, binary/streaming/
  realtime. Requires code + registration.

Authoring a Hermes skill is covered by the `hermes-skill-authoring` skill. Skills
load on demand via progressive disclosure (`skill_view` / `skills_list`), and the
**Curator** does background maintenance (usage tracking, staleness, archival,
LLM review) of agent-created skills.

## Gateway internals (the messaging gateway)

The gateway is the long-running process connecting Hermes to 14+ platforms.

**Key files** (`gateway/`): `run.py` (`GatewayRunner` — main loop, slash commands,
dispatch), `session.py` (`SessionStore`, `build_session_key()`), `delivery.py`
(outbound), `pairing.py` (DM auth), `hooks.py` (lifecycle hooks), `status.py`
(token locks), `platforms/` (one adapter per platform; `base.py` is shared).

**Message flow:**
1. Platform adapter normalizes a raw event → `MessageEvent`.
2. Base adapter's guard: if an agent is already running this session, queue the
   message + set an interrupt; `/approve`,`/deny`,`/stop` bypass inline.
3. `GatewayRunner._handle_message()` resolves the session key, checks auth, routes
   slash commands, intercepts running-agent commands, else creates an `AIAgent`
   and runs the conversation.
4. Response goes back out through the adapter.

**Session key format:** `agent:main:{platform}:{chat_type}:{chat_id}`
(e.g. `agent:main:telegram:private:123456789`). **Never build keys by hand — use
`build_session_key()`.** Thread-aware platforms fold thread IDs into `chat_id`.

**Authorization** (evaluated in order): per-platform allow-all flag →
platform allowlist → DM pairing (`/pair` → code → user authorized, persisted) →
global `GATEWAY_ALLOW_ALL_USERS` → default deny.

**Config sources:** `~/.hermes/.env` (keys/tokens), `~/.hermes/config.yaml`
(models/tools/display), env overrides. **Gotcha:** the gateway reads `config.yaml`
directly while the CLI uses `load_cli_config()` with hardcoded defaults — keys that
exist in CLI defaults but not in your file can behave differently between the two.

**Hooks** (Python modules under `gateway/builtin_hooks/` always-on, and
`~/.hermes/hooks/` user-installed; each is a dir with `HOOK.yaml` + `handler.py`).
Events: `gateway:startup`, `session:start|end|reset`, `agent:start|step|end`,
`command:*`. `agent:step`/`agent:end` are the clean place to **observe tool use and
latency for gateway-level testing** when the API surface won't show it.

**Delivery** (`delivery.py`): direct reply, home-channel routing (cron/background
results), explicit targets (`send_message` → `telegram:-100123…`), cross-platform.
Cron deliveries live in their own session, not mirrored into gateway history.

**Process mgmt:** `hermes gateway start|stop` (profile-scoped PID file at
`~/.hermes/gateway.pid`), `stop --all` (global scan, used on updates), or run under
systemd/launchd. Token locks (`acquire_scoped_lock`) stop two profiles sharing one
bot token.

## Where to read next (official docs)

- Skills: `/docs/user-guide/features/skills`, `/docs/developer-guide/creating-skills`
- Agent loop & prompt assembly: `/docs/developer-guide/agent-loop`, `/prompt-assembly`
- Gateway: `/docs/developer-guide/gateway-internals`, `/docs/user-guide/messaging`
- Providers/routing: `/docs/developer-guide/provider-runtime`, `/docs/user-guide/features/provider-routing`
- API server: `/docs/user-guide/features/api-server`
- Python library: `/docs/guides/python-library`
- Batch/trajectories: `/docs/user-guide/features/batch-processing`
- Delegation/subagents: `/docs/user-guide/features/delegation`
- Adding adapters/tools/providers: `/docs/developer-guide/adding-platform-adapters` (and `/adding-tools`, `/adding-providers`)

> Verify version-specific details (exact CLI flags like `-Q`, message-dict keys,
> tool names) against your installed build before relying on them in automation.

## CT 133 deployment realities (verified 2026-06-04)

Concrete, measured facts about the live CT 133 (`hermes`, 192.168.1.33) install.
Treat these as ground truth for that box; re-verify before porting elsewhere.

### Editable install = the checked-out branch IS the running code
Hermes runs as an **editable install** at
`/opt/hermes/home/.hermes/hermes-agent`. There is no build step between the git
working tree and the running agent — **the checked-out git branch is the running
code.** It must stay on branch **`integrated`** (it carries profile-aware
delegation). **Two prior outages** were caused by an agent `git checkout`-ing a
feature branch on the live tree. Do dev work in a `git worktree` or under
`hermes -p dev`, never by switching branches on the live checkout.

### Two config files that drift (fixes must hit BOTH)
| File | Read by |
|---|---|
| `/opt/hermes/home/config.yaml` | the **gateway** (Telegram / messaging serving path) |
| `/opt/hermes/home/.hermes/config.yaml` | the **TUI / CLI** (`HERMES_HOME=/opt/hermes/home/.hermes`) |
A config fix applied to only one of these silently fails on the other surface.
This is the concrete instance of the general "gateway reads `config.yaml`
directly" gotcha above — **apply config changes to both files.**

### terminal AND code_execution are disabled → ops uses cluster-ops MCP
`agent.disabled_toolsets` globally disables both `terminal` and
`code_execution`. Ops is therefore done through the **cluster-ops MCP**, not by
shelling out. Tools: `service_status`, `journal_tail`, `disk_usage`,
`docker_ps`, `exec_raw`, `cluster_snapshot`. The **`ops` profile** carries
`mcp-cluster-ops`; the **orchestrator** (cli/telegram platforms) does **not** by
default. (Routing rules: see the `hermes-orchestration-routing` skill.)

### weather MCP + default location
weather MCP tools: `get_current_conditions`, `get_forecast`, `search_location`,
`get_alerts`. **Default location: Woodstock IL 60098** (lat 42.3147,
lon -88.4487). Identity-context gap: a bare "what is the weather" only resolves
to Woodstock if the identity/profile context supplying that default is present —
stripping context files (e.g. `skip_context_files=True`) can make the agent ask
"which city?" instead.

### Model + endpoints
Main model **`apex-fast:latest`** via `provider:custom` → Ollama
`http://192.168.1.28:11434/v1`. Gateway HTTP API on **~port 8643**. TUI/CLI
`HERMES_HOME=/opt/hermes/home/.hermes`. Measured routing compliance for
apex-fast: **80% overall, 100% on the direct-vs-delegate axis** (the one that
matters for the delegation-explosion anti-pattern).

### tool_search dynamic loading — the deferral tradeoff
`tool_search` dynamic loading is ON (`enabled: auto`, `threshold_pct: 2.0`): MCP
tool schemas are deferred behind 3 bridge stubs and fetched on demand. This saves
prefill tokens but adds a discovery round-trip. On a **slow local model** (P40)
the extra round-trip can be **net-negative** — the prefill savings don't pay for
the added turn. Worth measuring per workload.

### Latency profile (warm vs cold)
Cold **one-shot CLI** is the slowest path (MCP init on every run). **Warm
gateway / interactive** is the daily driver and what you should benchmark for
real usage. **Prefill dominates latency on the P40.** vLLM is likely **not
viable** on the P40 (Pascal, sm_61); **llama.cpp `-fa`** is the validated path.

### Gateway PID/lock symlinks must be recreated after a dashboard restart
After restarting via the dashboard, recreate the PID/lock symlinks the TUI/CLI
home expects:
```bash
ln -sf /opt/hermes/home/gateway.pid  /opt/hermes/home/.hermes/gateway.pid
ln -sf /opt/hermes/home/gateway.lock /opt/hermes/home/.hermes/gateway.lock
```
(Profile-scoped PID files live under `~/.hermes/gateway.pid`; the two homes must
agree or `hermes gateway stop/status` looks at the wrong file.)
