# The `integrated` branch as a personal distribution

`integrated` is not a feature branch — it is a **release artifact**: upstream
`NousResearch/hermes-agent` main plus a curated stack of feature PRs, rebuilt
deterministically by the upgrade driver. Treat it like a downstream distribution.

## Cadence

- **Weekly rebase** onto `upstream/main` via `upgrade-hermes.sh`. Upstream moves
  fast; a stale `integrated` accumulates harder conflicts. Smaller, frequent rebases
  beat one big quarterly merge.
- Run the driver any time an upstream fix you want lands, or a fork PR is finished
  and ready to fold in.

## CHANGELOG.integrated.md discipline

`CHANGELOG.integrated.md` is the human record of what this distribution adds on top
of upstream and why. Update it when:

- a feature branch is added to or dropped from `FEATURE_BRANCHES`,
- a conflict resolution makes a non-obvious choice (e.g. dropping
  `feat/delegation-profile-toolset-isolation` pending reconciliation),
- the post-deploy procedure changes.

It is the first thing future-you reads when the stack confuses you. Keep it honest
about what is DROPPED and why, not just what is merged.

## Plugin escape hatch (prefer it over core patches)

Every core patch carried in `integrated` is a recurring merge-conflict liability at
upgrade time. Before adding a feature branch to the distribution, ask whether the
behavior can ship as a **plugin or a skill** instead:

- A Hermes **skill** (instructions + shell + existing tools) needs zero core changes
  and never conflicts on rebase.
- A **plugin / hook** (`~/.hermes/hooks/`) can add gateway-level behavior without
  patching `gateway/run.py`.

Reserve core patches in `integrated` for changes that genuinely cannot be expressed
as a plugin/skill (e.g. provider-runtime or delegation-engine changes). The fewer
core patches in the stack, the cheaper every weekly rebase.

## Feature-branch hygiene

- Every branch in `FEATURE_BRANCHES` must exist on `origin` (the driver preflights
  this and aborts if one is missing).
- Keep the array's order meaningful: stacked chains (mga-1..mga-6) in ascending
  order; dependent branches after their dependencies.
- When you drop a branch, leave a comment in the array explaining why — the driver's
  `FEATURE_BRANCHES` block is the canonical place for that note.
