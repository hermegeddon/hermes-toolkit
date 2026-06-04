# The tool_search deferral tradeoff (pin vs defer)

`tool_search` dynamic loading is ON on CT 133 (`enabled: auto`, `threshold_pct:
2.0`). Understand exactly what it trades before tuning it.

## What it does

Instead of putting every MCP tool's full schema into the prompt, `tool_search` defers
schemas behind **3 bridge stubs**. When the agent needs a tool, it issues a discovery
call to fetch the real schema, then calls the tool.

- **Saves:** prefill tokens — fewer schemas in context every turn.
- **Costs:** a **discovery round-trip** — an extra model turn to find/load the tool
  before it can be used.

## Why it's a genuine tradeoff on the P40

On a fast cloud model the discovery round-trip is cheap and the prefill savings win.
On the **slow P40**, the extra turn is expensive (prefill dominates every turn), so
for tools the profile uses **constantly**, the discovery round-trip often costs more
than the prefill it saved. Net-negative.

## The rule

- Tools a profile uses on **most** tasks (e.g. the cluster-ops ops tools for an ops
  profile, weather for a weather-heavy profile): **pin** them so they load up front.
  Pay the prefill once per prompt instead of a discovery turn per use.
- Tools a profile uses **rarely**: leave them deferred behind tool_search. The prefill
  savings are real and you seldom pay the discovery cost.

## How to decide per workload

1. Identify the profile's hot tools (the ones a typical task calls).
2. Pin those; leave the long tail deferred.
3. Measure with the warm path (and the `hermes-eval-harness` skill) — compare
   end-to-end turns/latency with the hot tools pinned vs deferred. Keep whichever is
   faster for that workload.

Do NOT globally disable tool_search to "fix slowness" — that re-inflates prefill with
the entire long tail of rarely-used schemas. The win is selective pinning, not an
all-or-nothing flip.
