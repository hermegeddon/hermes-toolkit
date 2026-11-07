---
name: hermes-eval-harness
description: >-
  Fast, parallel QA for a Hermes Agent instance using the bundled hermes_eval.py
  harness instead of slow serial `hermes chat` CLI calls. Use this skill WHENEVER
  you need to test, benchmark, smoke-test, regression-test, or QA a Hermes agent
  or a Hermes skill/gateway — including "run the QA pass", "is the live instance
  healthy", "did that change break anything", or any request to verify Hermes
  behavior at more than one prompt. Strongly prefer this over shelling out to
  `hermes chat -Q -q` one prompt at a time. Integrates with the hermes_toolkit
  MCP (hermes_eval_suites_list / hermes_eval_start).
---

# Hermes Eval Harness

`scripts/hermes_eval.py` runs a whole suite of test prompts against Hermes
**concurrently** and emits a structured report. It exists to kill the slow
pattern of one cold-start CLI call per check.

## Setup

The harness lives in this skill's `scripts/` directory; nothing needs to be
installed elsewhere. If you wire it into an MCP or wrapper (for example a
`hermes_toolkit` MCP exposing `hermes_eval_suites_list` / `hermes_eval_start`),
point that integration at this `scripts/` path.

Prereqs: `pip install pyyaml` in the interpreter you run the harness with. For
the `library` backend use the **Hermes venv python** (the one that has the
`hermes-agent` packages; system python lacks them) and set `HERMES_HOME` to the
deployed config you want mirrored. When Hermes runs in WSL and you drive it from
Windows, `scripts/hermes_eval_wsl.cmd` wraps all of this (`HERMES_WSL_DISTRO`,
`HERMES_WSL_PYTHON=<venv python>`, `HERMES_WSL_HERMES_HOME`); suite paths passed
to it must be WSL paths (`/mnt/c/...`).

## The anti-pattern this replaces

```bash
# DON'T: serial, full process+memory+context startup per check, no structure
time timeout 90 hermes chat -Q -q "say hello"
# DO: one command, parallel, structured, diffable
python scripts/hermes_eval.py --suite scripts/suites/smoke.yaml --backend library --workers 6
```

## Backends — pick by what you're testing

| Backend | Speed | Tool-call assertions? | Use when |
|---|---|---|---|
| `library` | fastest | **yes** (full trajectory) | default; agent/skill behavior, tool routing |
| `api` | fast | no (final text only) | true end-to-end through the serving path — `--base-url http://<host>:<port>/v1`, key from the server's `API_SERVER_KEY` |
| `cli` | slow | no | A/B baseline against the old way only |

```bash
# library (in-process, parallel) — mirrors the DEPLOYED config by default
HERMES_HOME=<deployed hermes home> \
  python scripts/hermes_eval.py --suite scripts/suites/smoke.yaml --backend library --workers 6

# api (end-to-end through the API server)
python scripts/hermes_eval.py --suite scripts/suites/gateway.yaml \
  --backend api --base-url http://<host>:<port>/v1

# regression gate: diff against a saved report, write artifacts
python scripts/hermes_eval.py --suite 'scripts/suites/*.yaml' \
  --baseline last_report.json --out report.json --md report.md
```

Why `library` is fast: it constructs `AIAgent` with `quiet_mode=True`,
`skip_memory=True`, `skip_context_files=True`, and a low `max_iterations`, so
each call skips the overhead that dominates a cold CLI start, and the
`ThreadPoolExecutor` runs `--workers` cases at once (one fresh `AIAgent` per
task — the agent is not thread-safe to share).

### Mirroring the DEPLOYED agent (`--use-deployed-config`, default ON)

By default the `library` backend loads the **deployed** config — model, provider,
`base_url`, `api_key`, toolsets — via the project's own loader
(`hermes_cli.config.load_config`, honoring `HERMES_HOME`), so an in-process run
mirrors the agent you actually ship. It prints the resolved model up front:

```
[library] effective model: <deployed model>  (from deployed config)  endpoint=…  HERMES_HOME=…
```

This closes the config-drift trap: without it the harness sends a *generic*
model name at whatever endpoint `AIAgent` resolves internally and 404s, making
the eval meaningless. Point `HERMES_HOME` at the deployed config.

**Overrides (precedence, highest first):** explicit `--model` → a suite's
`defaults.model` / per-case `model` → the deployed model. Per-case `toolsets` /
`disable_toolsets`, `--base-url`, `--api-key` likewise override. The speed knobs
(`skip_memory` / `skip_context_files` / low `max_iterations`) are **always**
applied — deployed *brain and tools*, not its memory/context-file behavior.
`--bare` (alias `--no-deployed-config`) restores the bare-default behavior.

## Choosing the eval model (important)

The model you grade against is part of the test — pick it deliberately.

- **The default is a generic fallback, not a guarantee.** With neither `--model`
  nor `HERMES_QA_MODEL` set: on `library` runs with deployed-config mirroring the
  harness adopts the deployed model and says so (`[eval] bare run: adopting
  DEPLOYED model …`); on `api`/`cli`/`--bare` runs it falls back to a built-in
  generic model and prints an `[eval][WARN]` — do not ignore that for
  safety-critical work. The report JSON records `summary.effective_model`.
- **Diagnostics land on stderr.** Hermes `quiet_mode` hijacks stdout when the
  first `AIAgent` is constructed — unflushed stdout is silently discarded and
  later prints surface via stderr. The harness therefore emits its
  provenance/WARN lines to stderr (flushed); when capturing output, capture
  BOTH streams (especially through `wsl.exe`, which scrambles stream identity).
- **Safety-critical skills MUST be eval'd against a representative runtime
  model.** Weak fallbacks read explicit refusal/POST-ONLY/auth rules and sail
  past them, and a weak `judge` rubric then scores the wrong behavior as fine —
  false passes. (This cost real time in a live session.)
- **What to pass instead.** For `library` runs the default
  `--use-deployed-config` already mirrors the deployed model —
  prefer that. For `api`/`cli` runs pass `--model` explicitly. Set
  `--judge-model` to a stronger grader than the model under test when judging
  safety behavior.
- **Companion safeguard.** Pair a representative model with run-time tool
  deregistration (e.g. deregistering `skill_manage`) so eval runs can never
  mutate skill files: correct *and* side-effect-free.

## Suite schema (YAML)

```yaml
suite: my-suite
defaults:                 # merged into every case; a case key overrides it
  max_iterations: 6
  timeout: 60
  toolsets: [web]         # -> AIAgent(enabled_toolsets=...)
cases:
  - id: web-lookup
    category: web
    prompt: "What's the top story on Hacker News right now?"
    assert:
      - { type: tool_called, tool: web_search }
      - { type: nonempty }
```

If a case has no `assert`, it defaults to `[no_error, nonempty]`.

## Assertion reference

| type | fields | passes when |
|---|---|---|
| `nonempty` | — | response has non-whitespace text |
| `contains` | `value` | substring present (`ignore_case: true` default) |
| `contains_any` / `contains_all` | `values` | at least one / all present |
| `not_contains` | `value` | substring absent (refusals, leak checks) |
| `regex` | `pattern` | `re.search` matches |
| `tool_called` / `not_tool_called` | `tool` | named tool present / ABSENT in trajectory (**library only**) |
| `tool_call_matches` | `tool`, `arg_regex` | named tool was called and normalized JSON arguments match the regex (**library only**) |
| `max_tool_calls` | `n` | total tool invocations ≤ N (**library only**) |
| `max_llm_calls` | `n` | model turns ≤ N (**library only**) |
| `no_error` | — | backend returned no exception/timeout |
| `latency_under` | `seconds` | wall-clock under N seconds |
| `judge` | `rubric`, `threshold` | a grader model scores ≥ threshold (opt-in; `--judge-model`) |

### Disposable Kanban worker fixtures

Library suites can set `kanban_fixture` on a case to exercise the real Kanban
tool schemas and handlers without touching a live board. The harness creates one
temporary SQLite task and workspace per case, substitutes `{kanban_task_id}` and
`{workspace}` in the prompt, and exposes the normal worker environment while the
case runs. Optional fixture files are relative to the disposable workspace:

```yaml
kanban_fixture:
  files:
    README.md: "fixture input\n"
```

Because worker scoping is carried in process environment variables, any suite
using these fixtures is serialized automatically even when `--workers` is greater
than one. This is intentional isolation, not a performance regression.

### Routing-compliance assertions (the anti delegation-explosion trio)

`not_tool_called`, `max_tool_calls`, and `max_llm_calls` exist to catch the
**delegation-explosion** anti-pattern: a simple deterministic op routed through
`delegate_task`, burning 6–17 model calls where ONE direct tool call (~1–3
turns) is correct.

```yaml
- { type: tool_called,     tool: terminal }          # calibrate to YOUR install
- { type: not_tool_called, tool: delegate_task }     # MUST NOT delegate
- { type: max_llm_calls,   n: 3 }
```

**Library backend only.** The `api`/`cli` backends return no trajectory, so
`llm_calls` reports `0` there — `max_llm_calls` would be a **false green**. Keep
the routing trio on `library`; see `suites/routing.yaml`. The console failure
line prints the llm-call count so a wander is visible at a glance.

## Calibration (do this once per install)

Suite tool-name assertions are **install-specific**. This install has terminal
plus MCPs like linear/gbrain/honcho/openpass — there is NO cluster-ops or
weather MCP (those appear in older homelab-calibrated examples). Before trusting
a suite: run one case, print `result["messages"]`, confirm the emitted tool
names (MCP tools may surface bare or prefixed), and adjust. The same applies to
`_extract_tool_names()` in `hermes_eval.py` — Hermes is roughly OpenAI-shaped
but keys drift across builds; that function is the one schema-coupled spot.

## Reading the report

Console shows per-suite and total pass rates, p50/p95/max latency, and an
itemized failure list. `--out report.json` saves everything; feed it back via
`--baseline` next run to surface **regressions**, **fixes**, and **slowdowns**
(>1.5× and >3s). Non-zero exit if any case fails — drop it straight into a
pre-deploy gate.

## Evaluating multi-model ensembles (MoA)

Before running an MoA suite, separate the target surfaces (persistent virtual provider, one-shot command, callable tool, and outer WebUI/orchestrator envelope) and record the active profile, `HERMES_HOME`, provider, and resolved preset. A tool-called assertion is routing evidence, not quality evidence. Every MoA benchmark must include the aggregator alone as a strong direct baseline.

Pitfalls:

- Inspect the runtime call path before interpreting token fields: an advisor-specific cap such as `reference_max_tokens` may be distinct from a generic `max_tokens` field.
- Report unresolved provider/model pricing as **unknown/unpriced**, never as zero or at the aggregator's rate.
- Confirm each target surface and advisor route is actually registered and healthy before the bakeoff.

For the full static-audit sequence, ablation matrix, complementarity measures, rubric, hard promotion gates, and trace/privacy discipline, see `references/moa-evaluation-methodology.md`.

## Recommended loop (failure-oriented)

1. Run `smoke.yaml` (`library`) → baseline `report.json`.
2. Expand with 10–15 representative prompts per capability you care about.
3. Make a change (skill edit, config, gateway tweak).
4. Re-run with `--baseline` → fix the highest-impact regression class first.
5. Re-run until clean; promote the new report to baseline.
