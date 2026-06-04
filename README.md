# hermes-toolkit

A Claude Code / claude-mpm toolkit for developing, testing, debugging, operating, and tuning NousResearch Hermes Agent installs.

---

## What's inside

The toolkit has two layers that work together:

**Claude layer** — specialist subagents and knowledge skills that install into `.claude/agents` and `.claude/skills`. They give Claude Code first-class support for every common Hermes workflow: writing skills, evaluating them, running the eval harness, debugging a misbehaving install, maintaining a personal fork, and guarding the live deployment.

**Hermes layer** — a fast parallel eval harness (`hermes_eval.py`) that drives a live Hermes instance directly via the Python library, the OpenAI-compatible API, or the CLI. It runs whole suites concurrently with structured assertions and regression tracking, replacing the slow pattern of one cold-start `hermes chat` call per check.

---

## Agents

Six Claude Code subagents, each scoped to one job. Copy them into `~/.claude/agents/` (global) or `.claude/agents/` (project).

| Agent | Description |
|---|---|
| `hermes-evaluator` | Benchmarks and QAs a whole live Hermes instance across capability categories (basic chat, tool use, weather, ops). Runs checks fast and in parallel with latency and regression tracking. |
| `hermes-gateway-engineer` | Engineers and extends the Hermes messaging gateway — platform adapters, lifecycle hooks, authorization, session routing, delivery, and provider routing. |
| `hermes-gateway-tester` | Tests a Hermes gateway end-to-end — the serving path, authorization, session routing, slash-command dispatch, delivery, and agent-through-gateway behavior — then drives a failure-oriented improvement loop. |
| `hermes-skill-developer` | Authors and edits skills for a Hermes Agent install, following the Hermes SKILL.md contract (frontmatter, conditional activation, env vars, template tokens). |
| `hermes-skill-evaluator` | Builds and runs evaluation suites for an individual Hermes skill, scores results, finds failure classes, and recommends concrete edits. |
| `hermes-deploy-guard` | Verifies and maintains the integrity of a live Hermes editable-install deployment where the checked-out git branch is the running code. |

---

## Skills

Eight Claude Code skills that encode Hermes-specific knowledge. Copy them into `~/.claude/skills/` (global) or `.claude/skills/` (project). Also includes a shared shell helper.

| Skill | Description |
|---|---|
| `hermes-eval-harness` | How to use the bundled `hermes_eval.py` harness: backends, assertion types, regression gating, config-mirror mode. |
| `hermes-internals` | Authoritative map of the four Hermes surfaces (CLI, library, API server, gateway), which to use for which job, and the AIAgent constructor knobs that control speed and cost. |
| `hermes-orchestration-routing` | How a Hermes orchestrator should route work: deterministic single-command ops go direct to a tool; reasoning/judgment work gets delegated with a structured goal and a verification gate. |
| `hermes-skill-authoring` | How to author Hermes skills: the SKILL.md frontmatter contract, body structure, conditional activation, env vars and credential files, template tokens, inline shell, and media delivery. |
| `hermes-deploy-guard` | The deployment invariant and recovery procedure for an editable install where the checked-out branch is the running code. |
| `hermes-fork-maintainer` | Maintain a personal Hermes fork: rebase feature branches onto upstream, rebuild an integration branch, resolve conflicts on the hot files, and enforce the editable-install invariant. |
| `hermes-debug` | Systematic ordered checklist for debugging a misbehaving Hermes install: wrong code running, config changes that don't take effect, provider 404s, ops queries that explode into many model calls, gateway issues. |
| `hermes-performance` | Tune a local-model Hermes for latency: gateway/interactive path vs cold CLI, trimming toolsets per profile, keeping the model resident, the 32k-vs-64k context VRAM tradeoff, compression offload, and GPU backend choice. |

**Shared helper:** `skills/_shared/hermes-triage.sh` — a read-only triage and deploy-invariant guard script used by both `hermes-debug` and `hermes-fork-maintainer`. Checks the branch invariant, two-config drift (gateway vs CLI config), and gateway PID/lock symlink consistency. Everything is overridable via `HERMES_*` environment variables (see the header comments).

---

## Install

**Agents** — copy to your Claude agents directory:

```bash
cp agents/*.md ~/.claude/agents/
# or project-scoped:
cp agents/*.md .claude/agents/
```

**Skills** — copy to your Claude skills directory:

```bash
cp -r skills/hermes-eval-harness skills/hermes-internals \
      skills/hermes-orchestration-routing skills/hermes-skill-authoring \
      skills/hermes-deploy-guard skills/hermes-fork-maintainer \
      skills/hermes-debug skills/hermes-performance \
      skills/_shared \
      ~/.claude/skills/
# or project-scoped:
cp -r skills/ .claude/skills/
```

**Python dependencies:**

```bash
pip install pyyaml
```

For the `library` backend (default, fastest — runs the agent in-process):

```bash
pip install git+https://github.com/NousResearch/hermes-agent.git
```

---

## Eval harness quickstart

The harness lives at `skills/hermes-eval-harness/scripts/hermes_eval.py`. Point it at a suite YAML and a backend:

```bash
# Smoke test against the deployed agent config (library backend, in-process, parallel)
HERMES_HOME=/path/to/your/.hermes \
python skills/hermes-eval-harness/scripts/hermes_eval.py \
  --suite skills/hermes-eval-harness/scripts/suites/smoke.yaml \
  --backend library \
  --workers 6

# End-to-end through the gateway API
python skills/hermes-eval-harness/scripts/hermes_eval.py \
  --suite skills/hermes-eval-harness/scripts/suites/gateway.yaml \
  --backend api \
  --base-url http://localhost:8080/v1

# Regression gate: compare against a saved baseline
python skills/hermes-eval-harness/scripts/hermes_eval.py \
  --suite 'skills/hermes-eval-harness/scripts/suites/*.yaml' \
  --baseline last_report.json \
  --out report.json --md report.md
```

**Bundled suites:** `smoke.yaml`, `smoke-deployed.yaml`, `gateway.yaml`, `routing.yaml`, `moa-e2e.yaml`.

**Assertion types:** `contains`, `not_contains`, `regex`, `tool_called`, `not_tool_called`, `max_llm_calls`, `latency_under` (seconds).

The `library` backend constructs `AIAgent` with `quiet_mode=True`, `skip_memory=True`, `skip_context_files=True`, and a low `max_iterations`, so each call avoids the overhead that dominates a cold CLI start. By default it mirrors the deployed config (model, provider, `base_url`, toolsets) via the project's own loader — so results reflect the agent you actually ship.

---

## Note on examples

Examples and the bundled triage script default to the author's homelab setup (CT 133, internal IPs, an Ollama host, an `apex-fast` model, the `davidgut1982` fork). Substitute your own fork URL, host, paths, and model. The skills mark setup-specific values as "this setup", and `hermes-triage.sh` accepts `HERMES_*` environment variable overrides for every default path and branch name.

---

## License

MIT. See [LICENSE](LICENSE).
