# Kickoff prompt

Paste this to your PM / main Claude session (it assumes the `hermes-toolkit`
agents and skills are installed in `.claude/agents/` and `.claude/skills/`).

---

We maintain a **Hermes Agent** install and I want investigation and QA to be fast,
structured, and knowledgeable — not a slow loop of `hermes chat -Q -q` calls.

You have five specialists and three skills for this. Use them:

- **hermes-skill-developer** — write/edit Hermes skills (Hermes SKILL.md format).
- **hermes-skill-evaluator** — build a suite for a skill, score it, fix failure classes.
- **hermes-evaluator** — QA/benchmark the whole live instance (chat, tools, weather, ops).
- **hermes-gateway-engineer** — change gateway adapters/hooks/auth/routing/delivery.
- **hermes-gateway-tester** — verify + harden the gateway end-to-end, then roll out.

Knowledge skills (load on demand): `hermes-internals` (the four surfaces + AIAgent
knobs + gateway internals), `hermes-skill-authoring` (the Hermes SKILL.md contract),
`hermes-eval-harness` (the parallel test harness).

**Hard rule — never QA by shelling out to `hermes chat -Q -q` one prompt at a
time.** Use `hermes_eval.py`: the in-process `library` backend for behavior + tool
checks, the `api` backend for the end-to-end gateway path, always in parallel,
always with assertions and a baseline diff.

**Default loop for any change:**
1. Establish/refresh a baseline (`--out report.json`).
2. Make the change via the relevant specialist.
3. Re-run the suite with `--baseline report.json`.
4. Fix the highest-impact regression class first; repeat until clean.
5. Promote the clean run to the new baseline.

Before relying on `tool_called` assertions, print one trajectory and confirm the
real tool names. Tell me which surface you're using and why.

To start: **run the smoke QA pass on the live instance and give me a verdict +
failures + latencies.** Then propose the 10–15 representative prompts per capability
we should add to make it a real regression suite.
