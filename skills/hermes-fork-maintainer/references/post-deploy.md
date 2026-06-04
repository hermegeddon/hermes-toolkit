# Post-deploy steps (the driver does NOT do these)

`upgrade-hermes.sh` stops after `pip install -e .` and the git pushes. The running
gateway is still the OLD process until it is restarted, and the TUI/CLI home expects
PID/lock symlinks that a restart can break. Run these AFTER every upgrade.

## 1. Restart the gateway

Restart via the dashboard, or the gateway CLI. After a restart, the profile-scoped
PID file moves and the two hermes homes can disagree about where it lives.

## 2. Recreate the gateway PID/lock symlinks

The gateway writes its PID/lock under `/opt/hermes/home/`, but the TUI/CLI home is
`/opt/hermes/home/.hermes/`. They must point at the same files or
`hermes gateway stop/status` inspects the wrong file:

```bash
sudo ln -sf /opt/hermes/home/gateway.pid  /opt/hermes/home/.hermes/gateway.pid
sudo ln -sf /opt/hermes/home/gateway.lock /opt/hermes/home/.hermes/gateway.lock
```

## 3. Post-deploy smoke (weather is the canary)

The validated end-to-end canary is a bare weather query, which exercises model
routing, the weather MCP, and identity context (default location Woodstock IL 60098):

```bash
# warm path through the running gateway/interactive surface, NOT a cold one-shot CLI
hermes chat -q "what is the weather"
# expect: resolves to Woodstock IL without asking "which city?"
```

If it asks "which city?", the identity/profile context that supplies the default is
missing — check that context files are being injected (not skipped). Use the
`hermes-eval-harness` skill for a repeatable smoke suite instead of eyeballing one
run.

## 4. Confirm the invariant held

```bash
sudo ~/.claude/skills/hermes-fork-maintainer/scripts/hermes-triage.sh --assert
# OK: ... is on 'integrated' and clean.
```

## Recovery: live tree ended up on the wrong branch

This is the outage scenario. The running agent is executing wrong code. Restore:

```bash
cd /opt/hermes/home/.hermes/hermes-agent
sudo git checkout integrated
sudo ./venv/bin/python -m pip install -e .   # re-pin the editable install to integrated
```

Then redo post-deploy steps 1–3.
