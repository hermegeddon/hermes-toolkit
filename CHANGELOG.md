# hermes-toolkit v2 — CHANGELOG

All changes are relative to the original `hermes-toolkit` package, and fold in
**verified, measured** findings from a real Hermes install (the author's homelab,
dated 2026-06-04). v2 was built entirely under `/tmp`; it does not touch
the live `/opt/hermes` install or git.

---

## A — Eval suites calibrated to the real Hermes build

**`skills/hermes-eval-harness/scripts/suites/smoke.yaml` (rewritten)**
- Replaced placeholder ops assertion `tool_called: terminal` — `terminal` AND
  `code_execution` are in `agent.disabled_toolsets` (globally disabled) and can
  never fire. Ops now asserts the **cluster-ops MCP**
  (`cluster_ops_service_status`, `cluster_ops_disk_usage`).
- Replaced weather placeholder `tool_called: web_search` with the **weather MCP**
  (`mcp_weather_get_current_conditions`, `mcp_weather_get_forecast`). Both the
  prefixed and bare emitted-name forms are noted in comments at each case.
- Switched weather cases to the build's **default location, Woodstock IL 60098**
  (lat 42.3147, lon -88.4487). Added `weather-implicit-default` ("what is the
  weather") which exercises the identity-context default resolution, with a
  comment flagging that `skip_context_files=True` can break it.
- Added `max_llm_calls` / `not_tool_called: delegate_task` guards on the ops and
  greeting cases (anti delegation-explosion).
- Added homelab env defaults in the header (model `apex-fast:latest` via Ollama
  `<your-ollama-host>:11434/v1`; gateway api `:8643`; `HERMES_HOME=/opt/hermes/home/.hermes`).

**`skills/hermes-eval-harness/scripts/suites/gateway.yaml` (rewritten)**
- Base URL default changed to `http://localhost:8643/v1` (homelab gateway port).
- Weather case named to Woodstock IL (avoids identity-context dependency over the
  gateway).
- Expanded the header to state that `max_llm_calls` / `tool_called` /
  `not_tool_called` **cannot** be verified on the api backend (no trajectory;
  `llm_calls` reports 0 — a false green) and to flag the orchestrator-vs-ops
  profile distinction for the ops case.

## B — New routing-compliance suite

**`skills/hermes-eval-harness/scripts/suites/routing.yaml` (new)**
- Encodes "match mechanism to task". Direct cases (`route-direct`):
  - "is the hermes-gateway service running?" → `cluster_ops_service_status`,
    `not_tool_called: delegate_task`, `max_llm_calls: 3`, `latency_under: 60`.
  - "how much disk is free?" → `cluster_ops_disk_usage`, not delegate.
  - "tail the gateway log" → `cluster_ops_journal_tail`, not delegate.
- Delegate cases (`route-delegate`):
  - "debug why the gateway keeps crashing and propose a fix" → `delegate_task`.
  - "plan a migration of the lore DB" → `delegate_task`.
- Header explains the delegation-explosion anti-pattern (6–17 calls / 90–150s for
  a query that should be one direct call) and that it runs library backend only.

## C — Harness extended (`hermes_eval.py`)

**`skills/hermes-eval-harness/scripts/hermes_eval.py`**
- New `_count_llm_calls(messages)` helper: counts `role == "assistant"` turns in
  the trajectory (the anti delegation-explosion / anti-wander metric).
- `CaseResult` gains an `llm_calls: int` field.
- All four backend return tuples widened from `(text, tools, err)` to
  `(text, tools, llm_calls, err)`; `run_library` populates the real count, the
  api/cli backends report `0` (no trajectory). `run_case` and the `_judge` call
  site updated to the new arity.
- New assertion types in `assert_one`:
  - `not_tool_called` (`tool`) — fail if the named tool WAS called (e.g. assert a
    simple ops query did NOT use `delegate_task`).
  - `max_tool_calls` (`n`) — total tool invocations ≤ N.
  - `max_llm_calls` (`n`) — model turns ≤ N (the headline anti-wander assertion).
  - All three documented as **library-backend only** in code comments.
- Console failure line now prints the llm-call count when non-zero, so a wander is
  visible at a glance.
- Existing assertions, backends, reporting, and baseline-diff are unchanged.

**`skills/hermes-eval-harness/SKILL.md`**
- Assertion-reference table extended with the three new types.
- New "Routing-compliance assertions (the anti delegation-explosion trio)"
  subsection with the canonical pattern and the library-only / false-green caveat.

## D — New knowledge skill: orchestration routing

**`skills/hermes-orchestration-routing/SKILL.md` (new)**
- Core rule: deterministic single-command facts → ONE direct cluster-ops call,
  never delegate, never KB-search first; reasoning/judgment/multi-step →
  `delegate_task`.
- Decision table mapping concrete asks to mechanisms.
- Notes `terminal`/`code_execution` disabled → use cluster-ops MCP; the `ops`
  profile carries it, the orchestrator does not.
- claude-mpm delegation primitives: pre-delegation resolution (one-sentence
  question / domain / deliverable), structured goal (TASK / CONTEXT / DELIVERABLE
  / DONE-WHEN), verification gate (raw output not a claim), anti-wander stop.
- Drop-in SOUL.md / AGENTS.md routing snippet, validated against apex-fast (100%
  on direct-vs-delegate).

## E — `hermes-internals` enriched

**`skills/hermes-internals/SKILL.md`**
- Appended a "homelab deployment realities (verified 2026-06-04)" section
  (existing content preserved). Covers: editable-install = running-branch (must be
  `integrated`, two prior outages from branch switches); the two drifting config
  files and which platform reads which; `terminal`/`code_execution` disabled →
  cluster-ops MCP for ops; weather MCP + Woodstock default + identity-context gap;
  model/endpoints (`apex-fast:latest`, local Ollama host, gateway `:8643`);
  the `tool_search` deferral tradeoff on slow local models; warm-gateway-vs-cold-CLI
  latency and the P40 vLLM-vs-llama.cpp note; gateway PID/lock symlink recreation.

## F — New agent + skill: deploy guard

**`agents/hermes-deploy-guard.md` (new, model: sonnet)**
- Verifies/maintains deployment integrity: branch == `integrated`, config parity
  across both files, PID/lock symlinks, post-restart weather smoke. Never
  `git checkout`s a feature branch on the live tree (uses worktree / `hermes -p
  dev`). Includes the exact recovery sequence.

**`skills/hermes-deploy-guard/SKILL.md` (new)**
- The invariant + the exact recovery commands (checkout integrated → `pip install
  -e .` → restart → recreate symlinks → smoke).

## Top-level / packaging

- `README.md`: added a "v2 improvements (homelab calibration)" section summarizing
  A–F; updated the tree diagram, the goals table (added deploy-guard row), the
  "six specialists" count, and the caveats (point at the calibration assumptions).
- Flat review-level mirrors kept in sync with their canonical sources:
  `README.md` (= package README), `SKILL.md` (= `hermes-internals/SKILL.md`),
  `hermes_eval.py` (= the harness).
- Repackaged: `/tmp/hermes-toolkit-v2.tar.gz` (`tar czf … -C /tmp/hermes-toolkit-v2
  hermes-toolkit`).

---

## Assumptions still needing LIVE verification

These are calibrated from session findings but should be confirmed against a
printed `result["messages"]` trajectory before trusting in automation:

1. **Exact emitted MCP tool-call names.** Suites assert the `cluster_ops_*` and
   `mcp_weather_*` forms. The MCP bridge may emit a different prefix
   (`mcp_cluster_ops_service_status` vs `cluster_ops_service_status`) or the bare
   tool name (`get_current_conditions`). Print one trajectory and adjust
   `_extract_tool_names()` / the `tool:` values to match. (Both forms are noted in
   the suite comments.)
2. **`delegate_task` emitted name.** The routing suite asserts `delegate_task` as
   the orchestrator's delegation tool name — confirm the exact name on this build.
3. **`max_llm_calls` thresholds.** `n: 3` for direct ops is a calibrated guess for
   apex-fast; measure the actual turn count of a healthy direct call and tighten
   or loosen. (The 6–17 call explosion is the measured failure; 1–3 is the
   measured-correct range, but the exact ceiling is build-dependent.)
4. **Latency budgets.** `latency_under` values (40s chat, 60s direct ops, 60–150s
   gateway) are sized for the warm-vs-cold and P40-prefill profile but not pinned
   to a specific percentile — re-baseline on the live box.
5. **Gateway port.** `:8643` is "~port 8643" from the session — confirm the exact
   listen port before wiring the api backend.
6. **System-prompt leak needle.** `gw-refusal-safety` asserts `not_contains: "You
   are Hermes"` — replace with a verbatim phrase from THIS build's system prompt
   for a real leak check.
7. **Weather identity-context default.** `weather-implicit-default` assumes the
   profile supplies Woodstock as the default with no city named; verify it resolves
   rather than asking "which city?", especially under `skip_context_files=True`.
