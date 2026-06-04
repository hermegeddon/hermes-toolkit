---
name: hermes-deploy-guard
description: >-
  The deployment invariant and recovery procedure for a Hermes editable install
  where the checked-out git branch IS the running code (editable-install pattern). Use this
  skill WHENEVER restarting, updating, or doing dev work near the live Hermes
  tree, when the gateway won't stop/status correctly after a dashboard restart, or
  when verifying the install hasn't drifted off the `integrated` branch. Encodes
  the exact recovery commands.
---

# Hermes Deploy Guard

A Hermes **editable install** has no build step: **the checked-out git branch is
the running code.** This skill is the invariant that keeps that safe and the exact
recovery when it breaks. (Background: `hermes-internals` → "Homelab deployment
realities".)

## The invariant

- Live checkout: `/opt/hermes/home/.hermes/hermes-agent`.
- It **MUST stay on branch `integrated`** (carries profile-aware delegation).
- **Never `git checkout` a feature branch on the live tree.** Two prior outages
  came from exactly that. Dev work goes in a `git worktree` or `hermes -p dev`.

## Two config files (fix both)

| File | Read by |
|---|---|
| `/opt/hermes/home/config.yaml` | gateway (Telegram / messaging) |
| `/opt/hermes/home/.hermes/config.yaml` | TUI / CLI (`HERMES_HOME=/opt/hermes/home/.hermes`) |

A config change in one only silently fails on the other surface.

## Health checks

```bash
# 1. branch integrity — must print: integrated
git -C /opt/hermes/home/.hermes/hermes-agent rev-parse --abbrev-ref HEAD

# 2. PID/lock symlinks (required after a dashboard restart)
ls -l /opt/hermes/home/.hermes/gateway.pid /opt/hermes/home/.hermes/gateway.lock
#    -> should point at /opt/hermes/home/gateway.{pid,lock}
```
Ops/service checks go through the **cluster-ops MCP** (`service_status`,
`disk_usage`, `journal_tail`, …) — `terminal`/`code_execution` are disabled on
this build.

## Recovery (run in order, only when drift/breakage is confirmed)

```bash
cd /opt/hermes/home/.hermes/hermes-agent
git checkout integrated                                   # restore running branch (the ONE allowed checkout)
pip install -e .                                          # re-link editable install
# restart the gateway (dashboard, or `hermes gateway stop --all` then start)
ln -sf /opt/hermes/home/gateway.pid  /opt/hermes/home/.hermes/gateway.pid    # recreate symlinks
ln -sf /opt/hermes/home/gateway.lock /opt/hermes/home/.hermes/gateway.lock
# verify end-to-end (Woodstock IL weather + chat should go green):
python ../hermes-eval-harness/scripts/hermes_eval.py \
  --suite ../hermes-eval-harness/scripts/suites/smoke.yaml --backend library --workers 4
```

## Verification

Done = branch is `integrated`, both config files agree, both symlinks resolve to
`/opt/hermes/home/gateway.*`, and the smoke suite passes weather + chat. Report
the raw outputs, not a "looks healthy" claim.
