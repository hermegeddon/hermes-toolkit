---
name: hermes-orchestration-routing
description: >-
  How a Hermes orchestrator should ROUTE work: match the execution mechanism to
  the task. Deterministic single-command ops (is X running, service status, disk,
  logs, docker ps, cluster health) go DIRECT to a cluster-ops tool — never
  delegate, never KB-search first. Reasoning/judgment work (debug+propose, plan,
  review+suggest, multi-step investigation) goes through delegate_task with a
  structured goal and a verification gate. Use this skill WHENEVER deciding
  "should I answer this directly, call one tool, or delegate?", when a simple ops
  query is burning many model calls / timing out, or when wiring routing rules
  into SOUL.md / AGENTS.md. Verified on the author's homelab build against apex-fast (100% on the
  direct-vs-delegate axis).
---

# Hermes Orchestration Routing

The single biggest lever on latency and reliability for a Hermes orchestrator is
**matching the execution mechanism to the task**. Get this wrong and a question
that should be one tool call becomes a 6–17 model-call delegation that wanders.

## The core rule

> **Deterministic single-command facts → ONE direct tool call.**
> **Reasoning / judgment / multi-step → `delegate_task`.**
> Never delegate a status check. Never KB-search before a `service_status` call.

This was measured on the author's homelab build: simple ops ("is the gateway running?") routed
through `delegate_task` cost **6–17 model calls / 90–150s** and sometimes timed
out; the correct path is a **single `cluster-ops.service_status` call** (~1–3
model turns). On the slow local model (apex-fast on a P40) the cost of getting
this wrong is severe because prefill dominates every extra turn.

## DIRECT vs DELEGATE — the decision table

| The ask | Mechanism | Why |
|---|---|---|
| "is hermes-gateway running?" | `cluster-ops` service_status — **direct** | deterministic, one command, one fact |
| "how much disk is free?" | `cluster-ops` disk_usage — **direct** | same |
| "tail the gateway log" | `cluster-ops` journal_tail — **direct** | same |
| "is the container up?" | `cluster-ops` docker_ps — **direct** | same |
| "give me cluster health" | `cluster-ops` cluster_snapshot — **direct** | one structured read |
| "debug why the gateway keeps crashing and propose a fix" | `delegate_task` | investigation + judgment, not one command |
| "plan a migration of the lore DB" | `delegate_task` | multi-step reasoning, a deliverable |
| "review this config and suggest improvements" | `delegate_task` | judgment + recommendations |

If the answer is a single fact obtainable by a single deterministic command,
**call the tool**. If it requires investigation, synthesis, a plan, or a
judgment call, **delegate**.

## Ops mechanism on this build: cluster-ops MCP (terminal is disabled)

`terminal` AND `code_execution` are in `agent.disabled_toolsets` — globally
disabled. Do **not** try to shell out for ops; it cannot fire. Use the
**cluster-ops MCP** instead:

| Tool | Use for |
|---|---|
| `service_status` | is service X running / active |
| `journal_tail` | tail a unit's log |
| `disk_usage` | free space |
| `docker_ps` | container state |
| `exec_raw` | a raw command when no structured tool fits (last resort) |
| `cluster_snapshot` | one-shot multi-host health |

Profile note: the `ops` profile carries `mcp-cluster-ops`; the default
orchestrator (cli/telegram platforms) does **not**. If ops tools are missing,
you're on the wrong profile — say so rather than delegating around it.

## When you DO delegate: claude-mpm delegation primitives

Delegation is correct for reasoning work — but unstructured delegation is how a
run wanders. Apply these primitives every time:

1. **Pre-delegation resolution** — before calling `delegate_task`, resolve:
   - the question in **one sentence**,
   - the **domain** (ops / dev / research / gateway / skill),
   - the **deliverable** (a fix, a plan, a verdict, a number).
   If you can't state all three, you're not ready to delegate.

2. **Structured goal** — pass a goal with four fields:
   ```
   TASK:        <the one-sentence ask>
   CONTEXT:     <what's known, paths, prior findings>
   DELIVERABLE: <exactly what to return>
   DONE-WHEN:   <the observable condition that ends the task>
   ```

3. **Verification gate** — require **raw output, not a claim.** "I restarted the
   gateway" is not acceptable; the `service_status` output showing `active
   (running)` is. Reject deliverables that assert success without evidence.

4. **Anti-wander stop** — terminate a delegation that has produced **no
   deliverable in N turns** (start N small, e.g. 8). A delegation with no
   progress is the explosion this whole skill exists to prevent.

## Drop-in SOUL.md / AGENTS.md routing snippet

Validated against apex-fast (100% on direct-vs-delegate). Paste into the
orchestrator's identity/context file:

```markdown
## Routing rule (match mechanism to task)

- A request for a single deterministic fact about a host or service — "is X
  running", service status, disk free, tail a log, container state, cluster
  health — is answered with ONE direct cluster-ops tool call. Do NOT delegate
  it. Do NOT KB-search first. Do NOT shell out (terminal is disabled).
    - is X running        -> cluster-ops service_status
    - disk free           -> cluster-ops disk_usage
    - tail/view a log     -> cluster-ops journal_tail
    - container state     -> cluster-ops docker_ps
    - cluster health      -> cluster-ops cluster_snapshot
- A request that needs investigation, synthesis, a plan, a review, or multiple
  steps — "debug and propose a fix", "plan a migration", "review and suggest" —
  is delegated via delegate_task with TASK / CONTEXT / DELIVERABLE / DONE-WHEN,
  and is not considered done until it returns raw evidence, not a claim.
- If a single status check is taking many model turns, you mis-routed: stop,
  make the one direct tool call instead.
```

## Verifying compliance

Use the `hermes-eval-harness` skill's `suites/routing.yaml` (library backend).
It asserts `tool_called: cluster_ops_*` + `not_tool_called: delegate_task` +
`max_llm_calls: 3` on the direct cases, and `tool_called: delegate_task` on the
reasoning cases. A direct case that delegates, or blows past the llm-call cap, is
the anti-pattern caught red-handed.
