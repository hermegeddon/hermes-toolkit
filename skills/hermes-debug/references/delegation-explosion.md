# Delegation explosion, tool-search deferral, warm-vs-cold, reasoning_effort

These four are the "it works but it's slow / it wanders" failures. They share a root:
the orchestrator spending model turns it didn't need to.

## Delegation explosion (the big one)

**Symptom:** a simple, deterministic ops question — "is the gateway running?", "how
much disk is free?", "tail the log" — costs **6-17 model calls / 90-150s** and
sometimes times out.

**Cause:** the orchestrator routed it through `delegate_task` instead of making one
direct tool call. On the slow local model (apex-fast on a P40) every extra turn is
expensive because prefill dominates latency, so a mis-route is severe.

**Fix:** deterministic single-command facts go DIRECT to a cluster-ops tool, never
through `delegate_task`, never KB-search first:

| Ask | Tool |
|---|---|
| is X running | `cluster-ops service_status` |
| disk free | `cluster-ops disk_usage` |
| tail/view a log | `cluster-ops journal_tail` |
| container state | `cluster-ops docker_ps` |
| cluster health | `cluster-ops cluster_snapshot` |

`terminal` AND `code_execution` are globally disabled, so the agent cannot shell out
— cluster-ops is the ops mechanism. The full routing contract is in the
`hermes-orchestration-routing` skill; measured routing compliance for apex-fast is
100% on the direct-vs-delegate axis when the routing rule is in the identity file.
If ops tools are missing entirely, you're on the wrong profile: the `ops` profile
carries `mcp-cluster-ops`; the default orchestrator does not.

## tool_search deferral (extra discovery round-trips)

**Symptom:** before a tool fires, the agent spends a turn "discovering" it.

**Cause:** `tool_search` dynamic loading is ON (`enabled: auto`, `threshold_pct:
2.0`): MCP tool schemas are deferred behind 3 bridge stubs and fetched on demand.
This saves prefill tokens but adds a discovery round-trip. On the slow P40 the extra
round-trip can be **net-negative** — the prefill savings don't pay for the added turn.

**Fix:** pin the tools a profile uses frequently so they are loaded up front rather
than deferred. Measure per workload — this is a tradeoff, not a universal win. (Tuning
detail lives in the `hermes-performance` skill.)

## Warm gateway vs cold one-shot CLI

**Symptom:** the same prompt is fast in the interactive TUI but slow as a
`hermes chat -q` one-shot.

**Cause:** the cold one-shot CLI re-initializes MCP (and everything else) on every
run. The warm gateway/interactive surface is the daily driver.

**Fix:** benchmark and run real work on the warm path; don't diagnose latency from
cold one-shots.

## reasoning_effort throttling

**Symptom:** runs feel throttled or reasoning is truncated.

**Cause/fix:** check the profile's `reasoning_effort` — too low chokes the model on
work that needs more, too high wastes turns on trivial asks. Match it to the profile's
job.

## Measuring the fix

Don't ship on one eyeballed run. The `hermes-eval-harness` skill's
`suites/routing.yaml` asserts `tool_called: cluster_ops_*` + `not_tool_called:
delegate_task` + `max_llm_calls: 3` on the direct cases — a direct case that delegates
or blows the call cap is the anti-pattern caught red-handed.
