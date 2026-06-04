# Hermes QA Baseline Report — routing-compliance
**Date:** 2026-06-04
**Backend:** library (in-process AIAgent)
**Model:** apex-fast:latest @ http://192.168.1.28:11434/v1
**Status:** PARTIAL — delegate cases timed out

## Summary
**0/5 passed (0%)** · p50 ~17.5s · p95 ~90s+ · max 90s+

| category | pass | rate |
|---|---|---|
| route-direct | 0/3 | 0% |
| route-delegate | 0/2 | 0% |

## Routing Baseline Table

| case | pass/fail | tool_calls | llm_calls | latency | notes |
|------|-----------|------------|-----------|---------|-------|
| route-service-status | FAIL | [terminal] | 2 | 17.5s | Used shell cmd not cluster_ops_service_status; did NOT delegate (good) |
| route-disk-free | FAIL | [terminal] | 2 | 9.9s | Used shell cmd not cluster_ops_disk_usage; did NOT delegate (good) |
| route-tail-gateway-log | FAIL | [process] | 1 | TIMEOUT >45s | Called process tool (wrong); 2nd LLM call stalled |
| route-debug-and-fix | FAIL | [] | 0 | TIMEOUT >90s | delegate_task spawns unbounded sub-agent (delegation-explosion) |
| route-plan-migration | FAIL | [] | 0 | TIMEOUT >90s | Inferred same as above — not individually measured |

## Key Findings

### 1. cluster_ops_* Tools Missing
The agent loads 34 tools but **none are cluster_ops_service_status, cluster_ops_disk_usage, or cluster_ops_journal_tail**.
These are the target tools Phase 1 will implement. Until they exist, route-direct cases always fail the `tool_called` assertion.

### 2. Fallback Behavior: terminal Shell
For ops queries, the agent falls back to the `terminal` tool (raw shell commands). This works functionally
but fails the harness assertions and represents the wrong routing pattern.

### 3. Delegation Explosion Confirmed
`delegate_task` IS available but spawning it creates an unbounded sub-agent loop that exceeds 90s.
The 5-case suite cannot complete within 400s because 2 delegate cases each hang indefinitely.

### 4. route-tail-gateway-log Anomaly
Agent tried `process` tool with `{"action": "log"}` (missing required `session_id`), got error,
then stalled on the second LLM call for >45s. Unique failure mode vs the other direct cases.

### 5. Harness Default Model Mismatch
Default model `anthropic/claude-sonnet-4.6` is NOT available on Ollama (192.168.1.28:11434).
Only `apex-fast:latest` is available. Must pass `--model apex-fast:latest --base-url http://192.168.1.28:11434/v1`.

## Working Harness Command
```bash
time timeout 400 env HERMES_HOME=/opt/hermes/home/.hermes \
  /opt/hermes/home/.hermes/hermes-agent/venv/bin/python \
  /opt/hermes/toolkit/skills/hermes-eval-harness/scripts/hermes_eval.py \
  --suite /opt/hermes/toolkit/skills/hermes-eval-harness/scripts/suites/routing.yaml \
  --backend library \
  --model apex-fast:latest \
  --base-url http://192.168.1.28:11434/v1 \
  --workers 5 \
  --timeout 60 \
  --out /opt/hermes/toolkit/routing.baseline.json \
  --md /opt/hermes/toolkit/routing.baseline.md
```
Note: Will still timeout at 400s wall due to delegate cases hanging.
