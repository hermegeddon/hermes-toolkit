# Windows + WSL Setup

This guide covers running hermes-toolkit from Claude Code on Windows when Hermes
itself lives inside WSL.

## Requirements

- **WSL2** (not WSL1). WSL2 auto-forwards Linux ports to `localhost` on the Windows
  side; WSL1 does not — its Linux IP is separate and you'd need `wsl hostname -I`
  to find it.
- A WSL distro with Hermes installed (`hermes` on PATH, Hermes Python packages
  importable).

## Which backends work from Windows

| Backend | Works from Windows? | Notes |
|---------|--------------------|-|
| `api`   | Yes (WSL2)         | Golden path. Start `hermes serve` in WSL first. |
| `cli`   | Yes (auto-wrapped) | `hermes_eval.py` prepends `wsl` automatically on Windows. |
| `library` | No               | Requires WSL Python. Use `hermes_eval_wsl.cmd` instead. |

## API backend (recommended)

Start the Hermes API server in WSL:

```bash
# inside WSL
hermes serve --port 8643
```

Then from Windows / Claude Code:

```powershell
python skills/hermes-eval-harness/scripts/hermes_eval.py `
  --suite skills/hermes-eval-harness/scripts/suites/smoke.yaml `
  --backend api `
  --base-url http://localhost:8643/v1
```

Or set `HERMES_QA_BASE_URL=http://localhost:8643/v1` so you can omit `--base-url`.

## Library backend via WSL launcher

The `library` backend does an in-process `from run_agent import AIAgent`, which
needs the Hermes Python package. Windows Python can't import a WSL-installed package,
so run the whole script under WSL Python instead:

```powershell
# from the hermes-eval-harness/scripts/ directory
.\hermes_eval_wsl.cmd --suite suites/smoke.yaml --backend library --workers 6
```

`hermes_eval_wsl.cmd` converts its own path to a WSL path with `wslpath` and runs
`<python> hermes_eval.py <args>` inside WSL — all three backends work from there.

If the Hermes packages live in a **venv** (the usual case — system `python3` can't
`import run_agent`), point the launcher at the venv interpreter, and forward
`HERMES_HOME` so the library backend loads the deployed config (the non-interactive
WSL shell often has `HERMES_HOME` unset):

```powershell
$env:HERMES_WSL_PYTHON      = "/home/<user>/.hermes/hermes-agent/venv/bin/python3"
$env:HERMES_WSL_HERMES_HOME = "/home/<user>/.hermes"
.\hermes_eval_wsl.cmd --suite /mnt/c/.../suites/smoke.yaml --backend library --workers 6
```

| Launcher env var | Purpose | Default |
|------------------|---------|---------|
| `HERMES_WSL_DISTRO` | Which WSL distro to target | default distro |
| `HERMES_WSL_PYTHON` | Interpreter inside WSL (use the Hermes venv python) | `python3` |
| `HERMES_WSL_HERMES_HOME` | Forwarded as `HERMES_HOME` (library backend needs it for the deployed config) | unset |

Suite paths handed to the launcher must be **WSL paths** (`/mnt/c/...`) — they pass
straight to WSL python, so Windows paths (with backslashes) won't resolve. The
launcher only auto-converts its own script path.

## Multiple WSL distros

If you have more than one distro installed, set `HERMES_WSL_DISTRO` to the one
that has Hermes:

```powershell
$env:HERMES_WSL_DISTRO = "Ubuntu"
# now both hermes_eval_wsl.cmd and the cli backend target that distro
```

Or pass `--wsl-distro Ubuntu` to `hermes_eval.py` for the `cli` backend.

## CLI backend

The `cli` backend (`--backend cli`) calls `hermes chat` as a subprocess.
`hermes_eval.py` detects `sys.platform == "win32"` and automatically prepends
`wsl` (plus `-d <distro>` if `HERMES_WSL_DISTRO` is set). No extra config needed.

## Bash scripts (hermes-triage.sh)

The triage scripts are bash — run them inside WSL:

```powershell
wsl bash skills/hermes-debug/scripts/hermes-triage.sh
wsl bash skills/hermes-fork-maintainer/scripts/hermes-triage.sh
```

Or open a WSL terminal and run them directly from there.

## Recovery / deploy commands

Any `hermes`, `git -C /opt/hermes/...`, `pip install -e`, or `ln -sf` commands
shown in the agent instructions (`hermes-deploy-guard`, etc.) must be run inside
WSL. The easiest way is to keep a WSL terminal open alongside Claude Code.
