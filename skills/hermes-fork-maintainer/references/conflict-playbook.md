# Conflict Playbook — rebuilding `integrated`

The upgrade driver rebases every feature branch onto `upstream/main`, then rebuilds
`integrated` by `--no-ff` merging them in a fixed order. Conflicts cluster on a few
hot files. **Rule of thumb: on files this fork owns, our version wins.** The
exceptions below are where "our version" needs a specific side.

## The hot files

| File | Why it conflicts | Default resolution |
|---|---|---|
| `tools/delegate_tool.py` | profile/toolset delegation — fork-owned behavior | our version wins, BUT see design-conflict below |
| `gateway/run.py` | gateway dispatch / profile routing | our version wins |
| `gateway/platforms/api_server.py` | active-run-agent registration | take mga-6 side (below) |
| `hermes_cli/config.py` | CLI config defaults / profile knobs | our version wins |
| `tests/tools/test_delegate_toolset_scope.py` | diverges ~750 lines | follow whatever side `delegate_tool.py` resolves to |

## Stacked-branch ordering constraint (mga-1..mga-6)

`feat/mga-1-agent-profile` .. `feat/mga-6-docs-fixes` are a **stacked PR chain**:
each was branched off the previous one, not independently off `upstream/main`. They
MUST appear in `FEATURE_BRANCHES` in ascending `mga-N` order, and each `mga-(N+1)`
must rebase onto the rebased `mga-N` — NOT merely onto `upstream/main`. Rebasing them
all flat onto `upstream/main` makes them conflict on the sequential merge.

## Known recurring conflict #1 — api_server.py (AUTO-resolvable)

`gateway/platforms/api_server.py`: `mga-5` and `mga-6` both touch the
`self._active_run_agents[run_id] = agent` registration inside `_run_and_close`.
**mga-6 is authoritative**: it moves the assignment INSIDE the
`with use_profile(agent_profile):` block and adds an explanatory comment.
**Always take the mga-6 (incoming) side of this hunk.**

## Known DESIGN conflict #2 — delegate_tool.py (NOT auto-resolvable)

`tools/delegate_tool.py` + `tests/tools/test_delegate_toolset_scope.py`:
`feat/delegation-profile-toolset-isolation` is a SECOND, evolved design for
profile/toolset delegation that **overlaps** the profile delegation already
introduced by the mga stack (commit `bbcb155b0`, "add agent_profiles config and
profile= param"). They are **competing implementations of the same feature**, not
additive changes:

- HEAD (mga stack) uses `_profile_overrides`.
- the branch uses `resolved_profile_name` / `profile_resolved_toolsets`.
- the test file diverges by ~750 lines.

**DO NOT blind-resolve by taking one side.** Example (this fork, as of 2026-06):
this branch is **intentionally DROPPED** from `integrated` (the driver's
`FEATURE_BRANCHES` stops the mga-related stack after `feat/tool-search-hybrid-rerank`
for this reason) — your stack and dropped-branch set will differ. Either
(a) leave it dropped until it is rebased on top of the mga stack and reconciled with
`bbcb155b0`, or (b) hand-merge it deliberately with a human decision. Do NOT re-add
it to `FEATURE_BRANCHES` without full reconciliation.

## Resolving by hand, then resuming

The driver aborts cleanly: a rebase failure leaves `integrated` untouched; a merge
failure leaves it mid-merge-aborted. To resolve:

```bash
cd /opt/hermes/home/.hermes/hermes-agent
sudo git checkout <failing-branch>
sudo git rebase upstream/main          # resolve conflicts -> git rebase --continue
sudo git checkout integrated           # ALWAYS return the live tree to integrated
sudo /opt/hermes/home/.hermes/upgrade-hermes.sh   # re-run the driver from the top
```

If the driver crashed mid-rebase and left the live tree parked on a feature branch,
`git checkout integrated` on the live tree FIRST — the running agent is currently
executing the wrong code — then resolve.
