# Deterministic-Intent Miner — propose-only flywheel

A **discovery tool** that mines Hermes conversation history for *candidate*
deterministic intents — user questions that could be answered in pure Python (or
a single bounded MCP call) **without an LLM**, the way the
[`intent-handlers-core`](../plugins/intent-handlers-core/) plugin already bypasses
time/date, disk-free, and service-status.

It is **PROPOSE-ONLY**. It generates no handler code and ships nothing. It finds
candidates; a human (via a filed kanban task) decides whether to build a handler.

## The flywheel

```
  state.db (read-only)
        │
        ▼
 [1] MINER  (cron: Mon 08:00, no_agent, ZERO LLM)
        │  embed → cluster → score → exclude-covered → gate
        │  stdout = ranked JSON + Markdown report
        ▼
 [2] REVIEWER  (cron: Mon 09:00, agent-mode, context_from=miner)
        │  strict determinism gate (a/b/c/d); REJECT on any doubt
        │  output-presence guard: empty miner output → exit, file nothing
        ▼
 [3] kanban task  (ACCEPTED candidates only)
        │  body = intent + DRAFT handler sketch (proposal)
        ▼
 [4] HUMAN reviews the task and decides to build the handler — or not.
```

Steps 1–3 are automated and **self-throttling**: the size gate keeps the loop
quiet until an intent is frequent enough to matter, so it activates on its own as
data grows. Step 4 (and any code that ships) is always human-driven.

## Cadence & gate

| Knob | Value | Why |
|---|---|---|
| Miner schedule | `0 8 * * 1` (weekly, Mon 08:00) | weekly is plenty; data accrues slowly |
| Reviewer schedule | `0 9 * * 1` (Mon 09:00, 1h after miner) | runs after the miner has produced fresh output |
| `--min-cluster` (cron) | **10** | the real bar: an intent must recur ≥10× to be worth bypassing |
| `--min-cluster` (seeding) | 5 | lower bar used for the manual seed run on ~270 prompts |
| `--threshold` | 0.83 | cosine clustering band 0.80–0.85 |

## Embedding endpoint

- Default: `nomic-embed-text-v2-moe` at `http://192.168.1.36:11434/v1/embeddings`
  (OpenAI-compatible, **768-dim**). Override with `--endpoint` / `--model` or the
  `MINER_EMBED_ENDPOINT` / `MINER_EMBED_MODEL` env vars.
- nomic-embed-text-v2 wants a recall prefix — the miner uses `search_document: `
  consistently for every prompt (override with `--prefix`).
- Embeddings are cached on disk (`~/.hermes/cache/deterministic_miner_embeddings.json`)
  so re-runs are nearly free.
- If the endpoint is unreachable and the cache is cold, the miner exits non-zero
  with an error in `meta` (the no_agent cron then delivers nothing → silent).

## Scoring & exclusions

Each cluster (one candidate intent) is scored on:

- **size** — total occurrences (frequency).
- **avg_llm_calls** — mean `api_call_count` of the sessions it appears in (the
  **cost proxy**; latency is deliberately NOT used — it is corrupt in this DB).
- **determinism** ∈ [0,1] — high when tool-call diversity is low, one tool
  dominates (or none), and no non-deterministic tool (`delegate_task`,
  `web_search`, `browser`, vision, …) appears.

Clusters whose representative matches an **already-covered** intent
(time/date, disk, service-status, weather) are excluded from the proposal list
and shown separately for transparency. Harness/self-prompt noise (eval probes,
the agent's own iteration-cap continuation message) is dropped at load time.

## Run it manually

```bash
# Seed / ad-hoc run (lower bar, full report):
python3 /opt/hermes/toolkit/tools/deterministic_miner.py \
    --db /opt/hermes/home/.hermes/state.db --min-cluster 5

# Exactly what the cron job runs (production bar):
python3 /opt/hermes/home/.hermes/scripts/deterministic_miner_run.py

# Offline unit checks:
python3 /opt/hermes/toolkit/tools/deterministic_miner.py --self-test
```

## How to add a handler when a candidate is ACCEPTED

The miner/reviewer never ship code. When a kanban task is accepted, a human:

1. Add a matcher + formatter to `intent-handlers-core` following the
   `time_core.py` pattern: an **end-anchored, keyword-gated**
   `is_<intent>_intent(text) -> bool` (with explicit false-positive guards that
   FALL THROUGH on any doubt) and an `answer_<intent>()` that sources only
   deterministic data (stdlib, or one bounded cluster-ops MCP call).
2. Wire it into the plugin's `pre_gateway_dispatch` hook + a slash command, and
   add unit tests (true set + false-positive set + fall-through).
3. Enable per `intent-handlers-core/HANDLERS.md` (both `config.yaml` files).

See [`../plugins/intent-handlers-core/HANDLERS.md`](../plugins/intent-handlers-core/HANDLERS.md)
for the determinism gate (a/b/c/d) and the deployment mechanism.

## Files

| Path | Role |
|---|---|
| `tools/deterministic_miner.py` | the miner (this repo, source of truth) |
| `~/.hermes/scripts/deterministic_miner_run.py` | thin cron wrapper (calls the miner) |
| `~/.hermes/cron/jobs.json` | the two cron jobs (miner + reviewer) |
| `~/.hermes/cache/deterministic_miner_embeddings.json` | embedding cache |

## Cron jobs (created)

| Job | id | Schedule | Mode |
|---|---|---|---|
| `deterministic-miner` | `10adde89ce36` | `0 8 * * 1` | no_agent (zero LLM) |
| `deterministic-miner-reviewer` | `14973226205f` | `0 9 * * 1` | agent (`context_from` miner, toolsets: kanban+file) |

Recreate them with the `cron.jobs.create_job` API (see commit message); the CLI
`hermes cron create` cannot set `context_from` / `enabled_toolsets`.
