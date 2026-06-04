---
name: hermes-performance
description: >-
  Tune a local-model Hermes for latency: run the warm gateway/interactive path not
  a cold one-shot CLI, trim toolsets per profile, pin frequently-used tools so they
  aren't deferred behind tool_search, keep the model resident, weigh the 32k-vs-64k
  context VRAM tradeoff, offload compression to a fast cloud model, and pick a GPU
  serving backend. Use this skill WHENEVER the request is "hermes is slow", "make
  the local model faster", "speed up the local model / GPU", "should I use vLLM or
  llama.cpp", "reduce prefill latency", or "tune toolsets/context for speed".
  Prefill dominates local-model latency, so prompt/tool-schema size is the primary
  knob. (Validated on P40/sm_61/apex-fast; the Pascal sm_61 constraint applies to
  any P40/P100 — substitute your GPU, model, and Ollama host.)
---

# Hermes Performance (local model)

When Hermes runs a local model on a modest GPU, **prefill (prompt processing)
dominates latency.** Every token of prompt and every tool schema in context is paid
on every turn. So the levers are, in priority order: (1) run warm, (2) shrink what's
in context, (3) keep the model resident, (4) pick the right serving backend.
(This setup: `apex-fast:latest` on a Pascal **P40** via Ollama at
`http://<OLLAMA_HOST>:11434/v1` — substitute your Ollama host.)

## The levers (priority order)

### 1. Run warm, not cold (biggest, free)
The cold **one-shot CLI** (`hermes chat -q`) re-initializes MCP and reloads the model
on every run — the slowest path. The **warm gateway / interactive session** is the
daily driver. Benchmark and run real work on the warm path. A "slow Hermes" complaint
is most often someone timing cold one-shots.

### 2. Trim toolsets per profile (shrinks prefill)
Every enabled toolset's schemas sit in the prompt and are re-prefilled each turn. Cut
them to what the profile actually needs:
- `hermes chat -t web` (or per-call `-t`) to enable only what's needed,
- `platform_toolsets` / `disabled_toolsets` in the profile config to lock the set.
Fewer schemas = less prefill = faster every turn. This is the highest-leverage
context cut on a slow GPU.

### 3. Pin frequently-used tools past tool_search (the deferral tradeoff)
`tool_search` dynamic loading is ON: MCP schemas are deferred behind 3 bridge stubs
and fetched on demand. It saves prefill tokens but **adds a discovery round-trip**.
On a slow GPU the extra round-trip is often **net-negative** for tools the profile
uses constantly. **Pin** those tools so they load up front instead of incurring a
discovery turn. This is a genuine tradeoff — see `references/tool-search-tradeoff.md`
before flipping it, and measure per workload.

### 4. Keep the model resident
Set `OLLAMA_KEEP_ALIVE=-1` so the model stays loaded in VRAM and you don't pay a
reload (a large, GPU-specific cost) on the first turn after idle.

### 5. Context length: 32k vs 64k (VRAM tradeoff)
A larger KV cache costs VRAM and slows prefill. On a 24 GB P40, `context_length: 32k`
leaves more headroom and is faster; `64k` only when a workload genuinely needs it.
Don't pay for 64k of KV cache you never fill.

### 6. Offload compression to a fast cloud model
History compression / summarization on the slow local model stalls the session.
Override the compression auxiliary model to a fast cloud model so compaction doesn't
block the main model.

## Serving backend (validated path)
- **llama.cpp** with `-fa` (flash attention) and `-ngl 99` (all layers on GPU) is the
  **validated** path on this P40.
- **vLLM `--enable-prefix-caching` is NOT viable on the P40**: Pascal is sm_61, and
  the kernels need sm_80+ (Ampere). The same constraint applies to any P40/P100. Don't
  reach for vLLM prefix caching on Pascal hardware.
- Details, flags, and why in `references/serving-backend.md`.

## The mental model
Because prefill dominates, the master knob is **how big the prompt + tool schemas
are** and **how many turns** a task takes. Lever 2 (trim toolsets) and lever 3 (pin
vs defer) both shrink/optimize that; the routing discipline in
`hermes-orchestration-routing` (don't delegate a status check) shrinks the turn count.
Performance work and routing work compound.

## Red flags / STOP
- Benchmarking cold one-shot CLI runs and concluding "the model is slow" → measure the
  warm path first.
- Enabling vLLM prefix caching on a P40/P100 to "speed it up" → it won't run (sm_61
  < sm_80). STOP.
- Cranking context to 64k "to be safe" → you just slowed every prefill. Use 32k unless
  the workload needs more.
- Globally disabling tool_search to "fix slowness" → it's a per-workload tradeoff, not
  a universal win; pin hot tools instead and measure.

## When NOT to use this skill
- The install is broken (wrong code, config drift, 404s, gateway dead) → `hermes-debug`
  FIRST; a broken install isn't a performance problem.
- Upgrading/rebuilding the fork → `hermes-fork-maintainer`.
- Choosing a surface or skill-vs-tool → `hermes-internals`.
