---
name: hermes-fork-maintainer
description: >-
  Maintain a personal Hermes fork: rebase feature branches onto upstream/main,
  rebuild an integration branch, resolve conflicts on the hot files
  (delegate_tool.py, gateway/run.py, hermes_cli/config.py, api_server.py), and
  enforce the editable-install invariant — the checked-out branch IS the running
  code, so the live tree must stay on the integration branch and dev happens in a
  worktree. Use this skill WHENEVER the request is "upgrade hermes", "pull
  upstream", "rebase the fork", "rebuild integrated", "a feature branch conflicts",
  "the upgrade script aborted", "open a PR against my hermes fork", or "the live
  tree is on the wrong branch". (Examples use the author's homelab setup —
  substitute your fork URL, host, integration-branch name, and feature-branch list.)
---

# Hermes Fork Maintainer

A personal Hermes fork is a **distribution**: upstream `main` plus a stack of
feature PRs merged into one integration branch. When the fork is deployed as an
**editable install**, there is no build step — **the checked-out git branch IS the
running code.** (This setup example: `git@github.com:<your-fork>/hermes-agent.git`, the
integration branch is **`integrated`**, the live editable install is at
`/opt/hermes/home/.hermes/hermes-agent` — substitute your own fork URL and host.)

## The deploy invariant (read first, every time)

> The live tree MUST stay on the integration branch (`integrated`).
> `git checkout <feature-branch>` on the live tree silently swaps the running
> agent's code. **Two prior outages were caused exactly this way.**

- NEVER `git checkout` a feature branch on the live checkout.
- Do dev/PR work in a **`git worktree`** (`/opt/hermes/dev/hermes-agent` is the
  existing dev split here) or under a dev profile.
- After ANY operation, confirm `git -C <live> branch --show-current` == `integrated`.
- Paths under `/opt/hermes/` are root-owned: run git/the upgrade script with `sudo`.

Run the integrity check before AND after any fork work (env-overridable; defaults
to this setup):

```bash
sudo ~/.claude/skills/hermes-fork-maintainer/scripts/hermes-triage.sh --assert   # exit 0 = safe
```

## Quick reference

| Task | Path |
|---|---|
| Live tree (must stay `integrated`) | `/opt/hermes/home/.hermes/hermes-agent` |
| Dev worktree (PR work happens here) | `/opt/hermes/dev/hermes-agent` |
| Upgrade driver (does steps 1–12) | `/opt/hermes/home/.hermes/upgrade-hermes.sh` |
| Distribution changelog | `CHANGELOG.integrated.md` |
| origin (push) | `davidgut1982/hermes-agent` |
| upstream (fetch only) | `NousResearch/hermes-agent` |

## The upgrade flow

The fork ships its own driver — **NEVER run `hermes update`** (it resets the clone
to origin/main and DESTROYS the integration branch). Run the driver instead:

```bash
sudo /opt/hermes/home/.hermes/upgrade-hermes.sh
```

It performs, in order: clean-tree check → preflight (every feature branch exists on
origin) → fetch upstream → exit early if `integrated..upstream/main` == 0 → **rebase
each feature branch onto upstream/main** → reset `integrated --hard upstream/main` →
**`--no-ff` merge each branch back in order** → pytest gate (`tests/tools tests/agent`)
→ `pip install -e .` → push `integrated` and the branches `--force-with-lease`.

**The driver stops at code only.** It does NOT restart the gateway, recreate the
PID/lock symlinks, or run the post-deploy smoke. Those are manual post-steps — see
`references/post-deploy.md`. Skipping them is how a "successful upgrade" still leaves
a broken gateway.

## When the driver aborts (conflicts)

The driver aborts cleanly and leaves `integrated` untouched on a rebase failure, or
mid-merge-aborted on a merge failure. Resolve in the live tree, then re-run the
driver. **"Our version wins"** on conflicts in files this fork owns. The recurring
conflict sites, the authoritative side for each, and the stacked-branch ordering
constraint (mga-1..mga-6) are in `references/conflict-playbook.md` — read it before
hand-resolving `delegate_tool.py` or `api_server.py`.

## Opening a PR against the fork

PR work NEVER touches the live tree. Use the dev worktree:

```bash
sudo git -C /opt/hermes/dev/hermes-agent fetch upstream
sudo git -C /opt/hermes/dev/hermes-agent checkout -b feat/my-change upstream/main
# edit, commit, push to origin, open PR with gh
```

To add a finished PR to the distribution, add its branch to `FEATURE_BRANCHES` in
the driver (respecting ordering) and re-run. See `references/distribution.md` for the
weekly-rebase cadence, the `CHANGELOG.integrated.md` discipline, and the plugin
escape hatch (ship behavior as a plugin/skill instead of a core patch when possible —
fewer merge conflicts at upgrade time).

## Reviewing a PR against the fork (governance-aware)

Review the fork's open PRs against the **fork's own rules first**, then code quality.
Review-only: comment, never `--approve` / `gh pr merge` (that is a human decision).

1. **Governance first.** Read the repo's CAPITALIZED rule files at the PR's head and
   check the PR against them, citing `file:line`:
   - `CONTRIBUTING.md` (one-logical-change-per-PR, commit style, required tests),
     `SECURITY.md`, `CODEOWNERS`, `.github/PULL_REQUEST_TEMPLATE.md` (is the body
     actually filled in?), `AGENTS.md` / `SOUL.md` (behavior/routing contract),
     `CHANGELOG.integrated.md`.
   - State any missing rule files explicitly — absence is not compliance.
2. **Then code quality:** correctness, security, error handling, performance, tests.
3. **Output two sections:** rule violations (with governance `file:line` citations)
   vs. quality suggestions, so a maintainer can tell a project-rule break from a
   taste call.

The highest-value finding is usually a governance break — e.g. a PR titled as one
change that also bundles unrelated core edits violates `CONTRIBUTING.md`
"one logical change per PR." Catch that before reviewing the diff line-by-line.

## Red flags / STOP

- `branch --show-current` on the live tree is NOT `integrated` → STOP, restore it
  (`scripts/hermes-triage.sh --assert` tells you; recovery in `references/post-deploy.md`).
- About to run `hermes update`, `git checkout <branch>`, or `git reset` on the live
  tree → STOP. Use the driver or a worktree.
- Driver aborted mid-rebase and the live tree is parked on a feature branch → STOP,
  `git checkout integrated` on the live tree FIRST (the running agent is currently
  wrong), then resolve the conflict.
- A conflict in `delegate_tool.py` / `api_server.py` that looks like two competing
  designs (not additive) → STOP, do not blind-resolve; see the conflict playbook
  (feat/delegation-profile-toolset-isolation is intentionally DROPPED for this).

## When NOT to use this skill

- Debugging a misbehaving install that you did NOT just upgrade → `hermes-debug`.
- Making it faster → `hermes-performance`.
- Deciding skill-vs-tool or which surface to drive → `hermes-internals`.
