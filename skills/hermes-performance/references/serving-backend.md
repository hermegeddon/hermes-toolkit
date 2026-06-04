# Serving backend on the P40

The GPU is an NVIDIA **P40** (Pascal, compute capability **sm_61**, 24 GB). Its
compute capability is the constraint that decides which backends and features work.

## llama.cpp — the validated path

Run apex-fast under llama.cpp with:

- `-fa` — flash attention. Cuts attention memory traffic; helps prefill, which is the
  P40 bottleneck.
- `-ngl 99` — offload all model layers to the GPU. The P40's 24 GB fits apex-fast at
  32k context with room to spare; keep everything on-GPU to avoid CPU offload stalls.

This combination is the **validated** configuration for this box. Pair it with
`OLLAMA_KEEP_ALIVE=-1` (or the equivalent resident-model setting) so you don't pay a
reload after idle.

## vLLM — prefix caching NOT viable

`vLLM --enable-prefix-caching` is attractive (it would cache the shared system
prompt + tool schemas across turns, directly attacking the prefill cost) but its
kernels require **sm_80+** (Ampere). The P40 is **sm_61** (Pascal), so the
prefix-caching path will not run here. Do not plan around vLLM prefix caching on this
GPU. If prefix caching becomes a hard requirement, it needs newer hardware, not a
config change.

## Why prefill is the bottleneck

On the P40, the prefill (prompt-processing) phase dominates per-turn latency more than
on newer GPUs. Practical consequences:

- The size of the **prompt + tool schemas** is the master latency knob — every token
  is re-prefilled each turn. (Trim toolsets, pin vs defer — see the main skill.)
- The **number of turns** a task takes multiplies the prefill cost — so routing
  discipline (don't delegate a deterministic status check) is also a performance lever.
- A bigger **KV cache** (64k vs 32k context) costs VRAM and slows prefill; default to
  32k.

## Quick backend decision

- Serving apex-fast on the P40            -> llama.cpp `-fa -ngl 99`, model resident.
- Want prefix caching to cut prefill      -> not on a P40 (sm_61); needs sm_80+ hw.
- Prefill still dominates after backend    -> attack prompt/tool-schema size and turn
  count, not the backend.
