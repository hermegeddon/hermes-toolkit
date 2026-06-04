---
name: hermes-eval-harness
description: >-
  Fast, parallel QA for a Hermes Agent instance using the bundled hermes_eval.py
  harness instead of slow serial `hermes chat` CLI calls. Use this skill WHENEVER
  you need to test, benchmark, smoke-test, regression-test, or QA a Hermes agent
  or a Hermes skill/gateway — including "run the QA pass", "test weather/ops/chat",
  "is the live instance healthy", "did that change break anything", or any request
  to verify Hermes behavior at more than one prompt. Strongly prefer this over
  shelling out to `hermes chat -Q -q` one prompt at a time.
---

# Hermes Eval Harness

`scripts/hermes_eval.py` runs a whole suite of test prompts against Hermes
**concurrently** and emits a structured report. It exists to kill the slow
pattern of one cold-start CLI call per check.

## The anti-pattern this replaces

```bash
# DON'T: serial, full process+memory+context startup per check, no structure
time timeout 90 HERMES_HOME=... hermes chat -Q -q "say hello"
time timeout 120 HERMES_HOME=... hermes chat -Q -q "weather in Chicago"
# ... minutes later ...
```

```bash
# DO: one command, parallel, structured, diffable
python scripts/hermes_eval.py --suite scripts/suites/smoke.yaml --backend library --workers 6
```

## Prerequisites

- `pip install pyyaml`
- For the `library` backend (default, fastest):
  `pip install git+https://github.com/NousResearch/hermes-agent.git` and the same
  env vars the CLI uses (`OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` / etc.).
- For the `api` backend: a running OpenAI-compatible endpoint (the gateway's
  `api_server` adapter or `hermes serve`). See the `hermes-internals` skill.

## Backends — pick by what you're testing

| Backend | Speed | Tool-call assertions? | Use when |
|---|---|---|---|
| `library` | fastest | **yes** (full trajectory) | default; testing agent/skill behavior, tool routing |
| `api` | fast | no (final text only) | true end-to-end through the gateway path |
| `cli` | slow | no | A/B baseline against the old way only |

```bash
# library (in-process, parallel)
python scripts/hermes_eval.py --suite scripts/suites/smoke.yaml --workers 6

# api (end-to-end through the gateway)
python scripts/hermes_eval.py --suite scripts/suites/gateway.yaml \
  --backend api --base-url http://localhost:8080/v1 --model anthropic/claude-sonnet-4.6

# regression gate: diff against a saved report, write artifacts
python scripts/hermes_eval.py --suite 'scripts/suites/*.yaml' \
  --baseline last_report.json --out report.json --md report.md
```

Why `library` is fast: it constructs `AIAgent` with `quiet_mode=True`,
`skip_memory=True`, `skip_context_files=True`, and a low `max_iterations`, so each
call skips the overhead that dominates a cold CLI start, and the
`ThreadPoolExecutor` runs `--workers` cases at once. (Always one fresh `AIAgent`
per task — the harness does this; the agent is not thread-safe to share.)

### Mirroring the DEPLOYED agent (`--use-deployed-config`, default ON)

By default the `library` backend loads the **deployed** config — model, provider,
`base_url`, `api_key`, and toolsets — via the project's own loader
(`hermes_cli.config.load_config`, honoring `HERMES_HOME`), so an in-process run
mirrors the agent you actually ship instead of a bare-default `AIAgent`. It prints
the resolved model up front:

```
[library] effective model: apex-fast:latest  (from deployed config)  endpoint=… HERMES_HOME=…
```

This closes the config-drift trap (see the `hermes-internals` skill): without it the
harness sends a *generic* model name (the `--model` default,
`anthropic/claude-sonnet-4.6`) while `AIAgent` still picks up the deployed
`base_url` internally — i.e. it points a model the endpoint doesn't serve at your
local Ollama box and 404s, making the eval meaningless. Point `HERMES_HOME` at the
config you want (`/opt/hermes/home/.hermes` for the TUI/CLI file, or the gateway's
`$HERMES_HOME/config.yaml`); the loader honors whichever you set — no path is
hardcoded.

**Overrides (precedence, highest first):** explicit `--model` → a suite's
`defaults.model` / per-case `model` → the deployed model. Per-case `toolsets` /
`disable_toolsets`, `--base-url`, and `--api-key` likewise override the deployed
values. The speed knobs (`skip_memory` / `skip_context_files` / low
`max_iterations`) are **always** applied — you get the deployed *brain and tools*,
not its memory/context-file behavior.

```bash
# default: mirror the deployed agent (apex-fast via the configured provider)
HERMES_HOME=/opt/hermes/home/.hermes \
  python scripts/hermes_eval.py --suite scripts/suites/smoke.yaml --backend library

# escape hatch: the OLD behavior — a bare-default AIAgent driven only by --model
python scripts/hermes_eval.py --suite scripts/suites/smoke.yaml --bare \
  --model anthropic/claude-sonnet-4.6
```

`--bare` (alias `--no-deployed-config`) restores the pre-deployed-config behavior;
the `api` and `cli` backends are unaffected by this flag (they use `--model`
verbatim).

## Suite schema (YAML)

```yaml
suite: my-suite
defaults:                 # merged into every case; a case key overrides it
  max_iterations: 6
  timeout: 60
  toolsets: [web]         # -> AIAgent(enabled_toolsets=...)
cases:
  - id: weather-explicit          # unique; used in reports + baseline diffing
    category: weather             # rolls up into per-category pass rates
    prompt: "What's the weather in Chicago?"
    toolsets: [web]               # or: disable_toolsets: [terminal, browser]
    assert:
      - { type: tool_called, tool: web_search }
      - { type: contains_any, values: ["°", "temperature"] }
```

If a case has no `assert`, it defaults to `[no_error, nonempty]`.

## Assertion reference

| type | fields | passes when |
|---|---|---|
| `nonempty` | — | response has non-whitespace text |
| `contains` | `value` | substring present (`ignore_case: true` default) |
| `contains_any` | `values` | at least one present |
| `contains_all` | `values` | all present |
| `not_contains` | `value` | substring absent (refusals, leak checks) |
| `regex` | `pattern` | `re.search` matches |
| `tool_called` | `tool` | named tool appears in the trajectory (**library only**) |
| `not_tool_called` | `tool` | named tool is ABSENT from the trajectory (**library only**) |
| `max_tool_calls` | `n` | total tool invocations ≤ N (**library only**) |
| `max_llm_calls` | `n` | model turns (assistant messages) ≤ N (**library only**) |
| `no_error` | — | backend returned no exception/timeout |
| `latency_under` | `seconds` | wall-clock under N seconds |
| `judge` | `rubric`, `threshold` | a grader model scores the response ≥ threshold |

`judge` is opt-in and only spins up a grader when a suite actually uses it. Set
`--judge-model` to grade with a cheaper/stronger model than the one under test.

### Routing-compliance assertions (the anti delegation-explosion trio)

`not_tool_called`, `max_tool_calls`, and `max_llm_calls` exist to catch the
**delegation-explosion** anti-pattern: a simple, deterministic op ("is X
running?") routed through `delegate_task`, burning 6–17 model calls over
90–150s and sometimes wandering/timeouting, where the correct behavior is ONE
direct tool call (~1–3 model turns).

```yaml
# A status check must be a single direct cluster-ops call, never a delegation:
- { type: tool_called,     tool: cluster_ops_service_status }
- { type: not_tool_called, tool: delegate_task }   # MUST NOT delegate
- { type: max_llm_calls,   n: 3 }                  # one direct call, not a 6-17 turn run
```

**Library backend only.** These three read the `run_conversation` trajectory.
`max_llm_calls` counts `role == "assistant"` messages via `_count_llm_calls()`
(one per model turn). The `api` and `cli` backends return only final text and no
trajectory, so `llm_calls` is reported as `0` there — `max_llm_calls` would then
be a **false green**. Keep the routing trio on `library`; see `suites/routing.yaml`
for the full pattern. The console failure line now also prints the llm-call count
(`✗ [route-direct] route-service-status (142.0s, 11 llm calls) — …`) so a
wander is visible at a glance.

## Reading the report

Console shows per-suite and total pass rates, p50/p95/max latency, and an
itemized failure list (`✗ [category] id (latency) — which assertion failed`).
`--out report.json` saves everything; feed it back via `--baseline` next run to
surface **regressions** (newly failing ids), **fixes**, and **slowdowns**
(>1.5× and >3s slower). The process exits non-zero if any case fails — drop it
straight into a pre-deploy gate.

## The one schema-coupled spot

`tool_called` relies on `_extract_tool_names()` reading the `run_conversation`
message trajectory. Hermes is roughly OpenAI-shaped but keys drift across builds.
If tool assertions fail unexpectedly, print one `result["messages"]` and adjust
the three lookups in that function to match your version. Everything else asserts
on output text and is schema-independent.

## Recommended loop (failure-oriented)

1. Run `smoke.yaml` (`library`) → get a baseline `report.json`.
2. Expand the suite with 10–15 representative prompts per capability you care
   about (chat, tools, the specific skills you ship).
3. Make a change (skill edit, config, gateway tweak).
4. Re-run with `--baseline` → fix the highest-impact regression class first.
5. Re-run until clean; promote the new report to baseline.
