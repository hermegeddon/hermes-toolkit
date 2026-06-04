---
name: hermes-debug
description: >-
  Systematically debug a misbehaving Hermes install: wrong/old code running after
  dev work, config changes that don't take effect, provider/model 404s, simple ops
  queries that explode into 6-17 delegated model calls, missing tools, "which
  city?" weather failures, the gateway looking dead after a restart, or runs that
  are mysteriously slow. Use this skill WHENEVER the request is "hermes is broken /
  acting weird", "my config change did nothing", "I get a 404 from the model", "a
  status check is taking forever / timing out", "the agent won't run terminal
  commands", "gateway stop says no gateway", or "weather asks which city". It runs
  an ordered symptom -> check -> fix checklist; branch-first is gotcha #1.
---

# Hermes Debug

A misbehaving Hermes install almost always traces to one of eight known causes.
Work the checklist **in order** — each step is cheap and the early ones catch the
most common (and most destructive) faults. Stop at the first one that explains the
symptom.

Run the fast triage first; it answers steps 1, 2, and 7 in one shot (env-overridable;
defaults to this CT 133 setup):

```bash
sudo ~/.claude/skills/hermes-debug/scripts/hermes-triage.sh
```

On this install, ops uses the **cluster-ops MCP** (`service_status`, `journal_tail`,
`disk_usage`, `docker_ps`, `exec_raw`, `cluster_snapshot`) — `terminal` AND
`code_execution` are in `agent.disabled_toolsets`, so shelling out from the agent
will not fire. Substitute your own ops mechanism if yours aren't disabled.

## The ordered checklist

| # | Symptom | Check | Fix |
|---|---|---|---|
| 1 | Old behavior persists; code "didn't change"; weird crashes after dev work | `git -C /opt/hermes/home/.hermes/hermes-agent branch --show-current` | must be `integrated`. If not, `git checkout integrated` + `pip install -e .`. **#1 gotcha — editable install: the branch IS the running code.** |
| 2 | Config edit had no effect on one surface | which file did you edit? | TWO config files drift — fix BOTH (see below). |
| 3 | Model returns 404 / provider error | profile's `provider` + `model` valid? | provider must exist; deepseek profiles need `provider: openrouter`; check credential-pool contamination. `references/provider-routing.md`. |
| 4 | A status check costs 6-17 model calls / times out | was it delegated? | simple ops must go DIRECT to a cluster-ops tool, never `delegate_task`. `references/delegation-explosion.md`. |
| 5 | Extra discovery round-trips before a tool fires | is `tool_search` deferring the needed tool? | pin frequently-used tools so they aren't deferred behind the bridge stubs. |
| 6 | Same prompt fast interactively, slow as a one-shot | warm vs cold path | cold one-shot CLI re-inits MCP every run; benchmark the warm gateway/interactive path. |
| 7 | `hermes gateway stop/status` says no gateway, but it's running | PID/lock symlinks | recreate the symlinks (see below). |
| 8 | Runs feel throttled / truncated reasoning | `reasoning_effort` setting | check the profile's reasoning_effort isn't choking the model. |
| — | "which city?" on a bare weather query | identity/profile context stripped | the Woodstock default needs context files injected; don't `skip_context_files`. |

## Step 1 — CHECK BRANCH FIRST (the #1 gotcha)

The live tree at `/opt/hermes/home/.hermes/hermes-agent` is an **editable install**:
the checked-out branch IS the running code. It must be `integrated`. A feature branch
left checked out = the agent is silently running different code. **Two prior outages
were this.** If drift is found, this is a fork-maintenance problem — fix it and see
the `hermes-fork-maintainer` skill (recovery: `git checkout integrated` then
`pip install -e .`).

## Step 2 — two config files that drift

| File | Read by |
|---|---|
| `/opt/hermes/home/config.yaml` | the **gateway** (Telegram / messaging serving path) |
| `/opt/hermes/home/.hermes/config.yaml` | the **TUI / CLI** (`HERMES_HOME=/opt/hermes/home/.hermes`) |

A change to only one silently fails on the other surface. If a config fix "did
nothing," you almost certainly edited the wrong one — **apply config changes to
BOTH.** The triage script diffs them and flags drift on SHARED keys only (it ignores
legitimately gateway-only keys like `smart_model_routing`, the `providers` block,
`platform_toolsets.telegram`, `display.*`, `_config_version`).

## Step 7 — gateway PID/lock symlinks after a restart

After a dashboard restart the two homes can disagree about the PID/lock location, so
`hermes gateway stop/status` looks at the wrong file and reports "no gateway":

```bash
sudo ln -sf /opt/hermes/home/gateway.pid  /opt/hermes/home/.hermes/gateway.pid
sudo ln -sf /opt/hermes/home/gateway.lock /opt/hermes/home/.hermes/gateway.lock
```

## Deeper symptoms

- Provider/model 404s, deepseek `provider: openrouter`, credential-pool
  contamination → `references/provider-routing.md`.
- Delegation explosion (simple ops routed through `delegate_task`), tool-search
  deferral, warm-vs-cold latency, reasoning_effort → `references/delegation-explosion.md`.
- Measuring whether a fix actually worked → the `hermes-eval-harness` skill
  (`suites/routing.yaml` catches the delegation anti-pattern red-handed).

## Red flags / STOP

- Tempted to `git checkout` a branch or run `hermes update` to "reset" a broken
  install → STOP. That is the outage cause, not a fix. Use `hermes-fork-maintainer`.
- Applied a config fix and "verified" on only one surface → not verified; check the
  OTHER config file and the OTHER surface.
- Claiming the gateway is restarted without raw `service_status` evidence → reject
  the claim, get the evidence (on this install, terminal+code_execution are in
  `agent.disabled_toolsets`, so cluster-ops is the only ops path).

## When NOT to use this skill

- You just ran an upgrade and want the deploy/rebuild steps → `hermes-fork-maintainer`.
- The install is correct but slow and you want tuning levers → `hermes-performance`.
- Architectural "which surface / skill-vs-tool" questions → `hermes-internals`.
- Routing rules for the orchestrator → `hermes-orchestration-routing`.
