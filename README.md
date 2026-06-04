# hermes-toolkit

A Claude Code / claude-mpm package of **specialists + skills + a fast test harness**
for working on a [Hermes Agent](https://hermes-agent.nousresearch.com) install:
developing skills, evaluating skills, evaluating the live agent, and engineering +
testing gateways.

It exists to fix one specific pain: QA that was being done by looping
`hermes chat -Q -q "…"` — serial, cold-start-per-call, slow, and unstructured. The
harness here runs whole suites **in-process and in parallel**, with assertions and
regression tracking, so a dozen checks finish in seconds instead of minutes.

## What's inside

```
hermes-toolkit/
├── agents/                         # Claude Code subagents  → ~/.claude/agents/
│   ├── hermes-skill-developer.md       # (1) develop skills
│   ├── hermes-skill-evaluator.md       # (2) evaluate skills
│   ├── hermes-evaluator.md             # (3) evaluate the live hermes instance
│   ├── hermes-gateway-engineer.md      # (4) engineer gateways
│   ├── hermes-gateway-tester.md        # (5) test & improve gateways
│   └── hermes-deploy-guard.md          # (6) v2: protect the live editable install
├── skills/                         # Claude Agent Skills    → .claude/skills/
│   ├── hermes-internals/               # the map: 4 surfaces, AIAgent knobs, gateway internals
│   │                                   #   (+ v2: CT 133 deployment realities)
│   ├── hermes-skill-authoring/         # the Hermes SKILL.md contract
│   ├── hermes-orchestration-routing/   # v2: match mechanism to task (direct vs delegate)
│   ├── hermes-deploy-guard/            # v2: deploy invariant + recovery commands
│   └── hermes-eval-harness/            # how to drive the harness…
│       └── scripts/
│           ├── hermes_eval.py          # …the harness itself (the speed fix)
│           └── suites/{smoke,gateway,routing}.yaml   # routing.yaml is v2
├── prompts/
│   └── kickoff.md                  # paste-in prompt to start the workflow
└── README.md
```

The **agents** are thin role-routers. The depth lives in the **skills** (shared
knowledge, loaded on demand) and the **harness**. This keeps each agent small and
avoids duplicating the Hermes knowledge across them.

## v2 improvements (CT 133 calibration)

v2 folds in verified, measured findings from running this toolkit against the
live CT 133 (`hermes`, 192.168.1.33) install. See `CHANGELOG.md` for the full
diff and the list of assumptions that still need live verification.

- **A — eval suites calibrated to the real build.** `smoke.yaml` / `gateway.yaml`
  drop the placeholder tool names (`web_search`, `terminal`) for the real ones:
  weather via the weather MCP (`get_current_conditions` / `get_forecast`, default
  location **Woodstock IL**), ops via the **cluster-ops MCP** — because `terminal`
  and `code_execution` are **disabled** on this build. Gateway endpoint defaults
  to `:8643`.
- **B — new `routing.yaml` suite.** Encodes "match mechanism to task": status /
  disk / log queries must be ONE direct cluster-ops call (`not_tool_called:
  delegate_task`, `max_llm_calls: 3`); debug-and-fix / plan SHOULD delegate. This
  is the suite that catches the **delegation-explosion** anti-pattern.
- **C — harness assertion types added** (`hermes_eval.py`): `max_llm_calls`,
  `not_tool_called`, `max_tool_calls` — to fail runs that wander or mis-delegate.
  Library backend only (they read the trajectory).
- **D — new `hermes-orchestration-routing` skill.** The verified routing rule
  (direct vs delegate) + claude-mpm delegation primitives + a drop-in
  SOUL.md/AGENTS.md snippet validated against apex-fast (100% on direct-vs-delegate).
- **E — `hermes-internals` enriched** with a "CT 133 deployment realities"
  section: editable-install = running-branch (`integrated`), the two drifting
  config files, disabled toolsets, tool_search deferral tradeoff, warm-vs-cold
  latency, PID/lock symlink recreation, weather default.
- **F — new `hermes-deploy-guard` agent + skill.** Protects the live editable
  install: verifies branch == `integrated`, recreates the gateway PID/lock
  symlinks, runs the post-restart smoke, and NEVER `git checkout`s a feature
  branch on the live tree.

**The core problem v2 targets:** simple ops ("is X running?") was being routed
through `delegate_task` — 6–17 model calls, 90–150s, sometimes wandering and
timing out — when the correct answer is a single direct `cluster-ops.service_status`
call. The routing skill, the routing suite, and the new assertions exist to make
that mis-route impossible to ship unnoticed.

## ⚠️ Two SKILL.md dialects — don't confuse them

- The files in `skills/*/SKILL.md` are **Claude / Anthropic Agent Skills**
  (frontmatter = `name` + `description`). They're consumed by Claude Code /
  claude-mpm and contain *knowledge about Hermes*.
- A **Hermes skill** (what you install into your Hermes agent) is a *different*
  artifact with richer frontmatter (`version`, `metadata.hermes`, `requires_*`, …).
  The `hermes-skill-authoring` skill teaches that format.

So: these skills are written in the Claude dialect; the skills you'll *produce for
Hermes* use the Hermes dialect. The authoring skill flags this explicitly.

## Install

**Claude Code (native):**
```bash
# project scope (recommended: check into the repo you manage Hermes from)
cp -r hermes-toolkit/agents/*           .claude/agents/
cp -r hermes-toolkit/skills/*           .claude/skills/
# or user scope:  ~/.claude/agents/  and  ~/.claude/skills/
```
Run `/agents` in Claude Code to confirm the six specialists are listed.

**claude-mpm:** drop the same files into the project's `.claude/agents/` and
`.claude/skills/` (claude-mpm resolves bundled → user → project, project wins), or
publish them to your own agent/skill source repo and add it:
```bash
claude-mpm agent-source add https://github.com/<you>/<your-agents>
claude-mpm skill-source add https://github.com/<you>/<your-skills>
```

**Harness deps:**
```bash
pip install pyyaml
pip install git+https://github.com/NousResearch/hermes-agent.git   # for the library backend
```

## Quickstart

```bash
cd hermes-toolkit/skills/hermes-eval-harness/scripts

# in-process, parallel, full tool visibility (the new QA pass)
python hermes_eval.py --suite suites/smoke.yaml --backend library --workers 6 \
  --out report.json --md report.md

# end-to-end through the gateway serving path
python hermes_eval.py --suite suites/gateway.yaml --backend api \
  --base-url http://localhost:8080/v1 --model anthropic/claude-sonnet-4.6

# regression gate against a saved baseline (exits non-zero on any failure)
python hermes_eval.py --suite 'suites/*.yaml' --baseline report.json
```

Or just talk to Claude: *"Run the Hermes QA pass"* routes to `hermes-evaluator`;
*"write a Hermes skill that …"* routes to `hermes-skill-developer`; *"test the
gateway"* routes to `hermes-gateway-tester`.

## How the specialists map to the five goals

| Goal | Specialist | Leans on |
|---|---|---|
| 1. develop skills | `hermes-skill-developer` | `hermes-skill-authoring`, `hermes-internals` |
| 2. evaluate skills | `hermes-skill-evaluator` | `hermes-eval-harness`, `hermes-skill-authoring` |
| 3. evaluate hermes | `hermes-evaluator` | `hermes-eval-harness`, `hermes-internals` |
| 4. engineer gateways | `hermes-gateway-engineer` | `hermes-internals` (gateway), `gateway/` source |
| 5. test/improve gateways | `hermes-gateway-tester` | `hermes-eval-harness`, `hermes-internals` |
| 6. guard the deploy (v2) | `hermes-deploy-guard` | `hermes-deploy-guard`, `hermes-internals` (CT 133 realities), `hermes-eval-harness` |

## Caveats (read before trusting it in automation)

- **Tool-call assertions are schema-coupled.** `tool_called` reads the
  `run_conversation` message trajectory; Hermes is roughly OpenAI-shaped but keys
  drift across builds. If those assertions misbehave, print one
  `result["messages"]` and adjust `_extract_tool_names()` in `hermes_eval.py`.
- **API backend = final text only.** The OpenAI-compatible endpoint won't expose
  intermediate tool calls. Use the `library` backend for tool checks, or observe
  the gateway's `agent:step`/`agent:end` hooks.
- **Verify version-specific details** (CLI flags like `-Q`, exact tool names) against
  your installed Hermes build. v2 calibrates the suites to the CT 133 build, but
  the **exact emitted MCP tool-call names are an assumption** (`cluster_ops_*` vs
  `mcp_cluster_ops_*`, bare `get_current_conditions` vs prefixed) — print one
  `result["messages"]` to confirm. See `CHANGELOG.md` → "Assumptions needing live
  verification".
- **Models named in examples** and the subagent `model:` fields are defaults. On
  CT 133 the agent model is `apex-fast:latest` (Ollama @ 192.168.1.28); the Claude
  subagent `model:` fields (sonnet/opus) drive the Claude Code specialists, not the
  Hermes agent under test. Change to whatever your setup uses.
