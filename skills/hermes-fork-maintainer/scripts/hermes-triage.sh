#!/usr/bin/env bash
#
# hermes-triage.sh — Shared read-only triage + deploy-invariant guard for a Hermes
# editable install. Used by BOTH the hermes-debug and hermes-fork-maintainer skills
# (each symlinks this file into its own scripts/ dir).
#
# It answers, in one read-only pass:
#   [1] branch invariant — the editable install means the checked-out branch IS the
#       running code, so the live tree MUST be on the integration branch. (This
#       subsumes the old check_branch.sh: pass --assert to get its strict exit codes.)
#   [2] two-config drift — gateway config vs TUI/CLI config drift silently; a
#       one-sided edit fails on the other surface. Only SHARED keys are compared
#       (see the allowlist below) so legitimately surface-specific keys aren't flagged.
#   [7] gateway PID/lock symlinks — the two homes must agree or stop/status looks at
#       the wrong file.
#
# Everything is env-overridable so it works on any Hermes fork/host. The defaults are
# the author's homelab setup; substitute your own paths/branch via the env vars.
#
#   REPO         live editable checkout       (default /opt/hermes/home/.hermes/hermes-agent)
#   EXPECTED     integration branch name      (default integrated)
#   GW_CONFIG    gateway config file          (default /opt/hermes/home/config.yaml)
#   CLI_CONFIG   TUI/CLI config file          (default /opt/hermes/home/.hermes/config.yaml)
#   HOME_DIR     gateway home (pid/lock src)  (default /opt/hermes/home)
#   HERMES_DIR   TUI/CLI home (pid/lock link) (default /opt/hermes/home/.hermes)
#
# Usage:
#   sudo /path/to/hermes-triage.sh            # full triage, exit 0 (report-only)
#   sudo /path/to/hermes-triage.sh --assert   # branch-only strict guard, exit 0/1/2/3
#                                             #   0=on branch & clean, 1=wrong branch,
#                                             #   2=dirty, 3=not a git repo
#
# Override example (proves parameterization):
#   sudo REPO=/srv/myfork EXPECTED=release /path/to/hermes-triage.sh
#
# Windows + WSL: run this script inside WSL, not from a Windows shell:
#   wsl bash /mnt/c/.../skills/hermes-fork-maintainer/scripts/hermes-triage.sh
#
set -uo pipefail

REPO="${HERMES_REPO:-${REPO:-/opt/hermes/home/.hermes/hermes-agent}}"
EXPECTED="${HERMES_BRANCH:-${EXPECTED:-integrated}}"
GW_CONFIG="${HERMES_GW_CONFIG:-${GW_CONFIG:-/opt/hermes/home/config.yaml}}"
CLI_CONFIG="${HERMES_CLI_CONFIG:-${CLI_CONFIG:-/opt/hermes/home/.hermes/config.yaml}}"
HOME_DIR="${HERMES_HOME_DIR:-${HOME_DIR:-/opt/hermes/home}}"
HERMES_DIR="${HERMES_DIR_OVERRIDE:-${HERMES_DIR:-/opt/hermes/home/.hermes}}"

# --- Config-drift allowlist ----------------------------------------------------
# Drift is only meaningful on keys SHARED by both surfaces — a one-sided edit to a
# shared key is the real "my config change did nothing" bug. We compare ONLY these
# shared top-level blocks/keys:
#   model            (model.default, model.provider)
#   delegation       (delegation.*)
#   agent_profiles   (agent_profiles.*.model)
#   disabled_toolsets
# Everything else is LEGITIMATELY surface-specific and must NOT be flagged, or it
# produces false positives that mask real drift. Deliberately EXCLUDED (gateway-only):
#   smart_model_routing  — gateway-only routing policy (TUI/CLI has no equivalent)
#   providers / provider.custom — gateway serving-path provider block
#   platform_toolsets.telegram  — Telegram surface, gateway-only
#   display.*            — gateway presentation knobs
#   _config_version      — schema-version stamp, surface-local
# Override the compared set with HERMES_SHARED_KEYS="model delegation agent_profiles".
SHARED_KEYS="${HERMES_SHARED_KEYS:-model delegation agent_profiles}"
SHARED_SCALARS="${HERMES_SHARED_SCALARS:-disabled_toolsets}"

# extract_shared <file> — emit only the shared top-level YAML blocks + scalars,
# so the diff is restricted to surface-shared keys. A top-level block is a line at
# column 0 ending in ':' whose key is in SHARED_KEYS, plus its indented children.
extract_shared() {
  local file="$1"
  awk -v keys=" ${SHARED_KEYS} " -v scalars=" ${SHARED_SCALARS} " '
    /^[A-Za-z_][A-Za-z0-9_]*:[[:space:]]*$/ {                # top-level block header
      k=$0; sub(/:.*/,"",k); inblk=(index(keys," " k " ")>0); print_if(inblk,$0); next
    }
    /^[A-Za-z_][A-Za-z0-9_]*:/ {                             # top-level scalar key: val
      k=$0; sub(/:.*/,"",k); inblk=0; print_if(index(scalars," " k " ")>0,$0); next
    }
    /^[[:space:]]+/ { if (inblk) print }                     # indented child of a block
    function print_if(c,l){ if(c) print l }
  ' "$file"
}

assert_mode=0
[[ "${1:-}" == "--assert" ]] && assert_mode=1

# --- branch check (shared core) ------------------------------------------------
branch_check() {
  if ! git -C "${REPO}" rev-parse --git-dir >/dev/null 2>&1; then
    echo "FAIL[3]: ${REPO} is not a git repository." >&2
    return 3
  fi
  local current
  current="$(git -C "${REPO}" branch --show-current 2>/dev/null || echo '<detached>')"
  if [[ "${current}" != "${EXPECTED}" ]]; then
    echo "FAIL[1]: live tree is on '${current}', expected '${EXPECTED}'." >&2
    echo "         The running agent code is WRONG. Restore it:" >&2
    echo "           sudo git -C ${REPO} checkout ${EXPECTED}" >&2
    echo "         then reinstall:  sudo ${REPO}/venv/bin/python -m pip install -e ${REPO}" >&2
    return 1
  fi
  if [[ -n "$(git -C "${REPO}" status --porcelain 2>/dev/null)" ]]; then
    echo "WARN[2]: live tree on '${EXPECTED}' but working tree is DIRTY:" >&2
    git -C "${REPO}" status --short >&2
    echo "         Commit/stash before upgrading (the driver refuses a dirty tree)." >&2
    return 2
  fi
  echo "OK: ${REPO} is on '${EXPECTED}' and clean."
  return 0
}

# --assert: strict branch guard with check_branch.sh-style exit codes.
if (( assert_mode )); then
  branch_check
  exit $?
fi

# --- full triage ---------------------------------------------------------------
problems=0
hr() { printf '%s\n' "------------------------------------------------------------"; }

echo "== Hermes triage =="
echo "   REPO=${REPO}  EXPECTED=${EXPECTED}"
hr

echo "[1] Live tree branch (must be '${EXPECTED}' — editable install: branch IS the code)"
if git -C "${REPO}" rev-parse --git-dir >/dev/null 2>&1; then
  cur="$(git -C "${REPO}" branch --show-current 2>/dev/null || echo '<detached>')"
  if [[ "${cur}" == "${EXPECTED}" ]]; then
    echo "    OK: on '${EXPECTED}'"
  else
    echo "    *** PROBLEM: on '${cur}', expected '${EXPECTED}' — RUNNING WRONG CODE ***"
    echo "    Fix: sudo git -C ${REPO} checkout ${EXPECTED} && reinstall editable"
    problems=$((problems+1))
  fi
  if [[ -n "$(git -C "${REPO}" status --porcelain 2>/dev/null)" ]]; then
    echo "    NOTE: working tree is dirty (uncommitted changes present)"
  fi
else
  echo "    ?? ${REPO} is not a git repo"
fi
hr

echo "[2] Config drift (gateway vs TUI/CLI — fixes must hit BOTH; surface-only keys ignored)"
echo "    gateway : ${GW_CONFIG}"
echo "    tui/cli : ${CLI_CONFIG}"
if [[ -f "${GW_CONFIG}" && -f "${CLI_CONFIG}" ]]; then
  # Compare ONLY the shared top-level blocks/keys (SHARED_KEYS + SHARED_SCALARS);
  # gateway-only blocks (smart_model_routing, providers, display, telegram, …) are
  # never extracted, so they can't produce false-positive drift.
  echo "    comparing shared keys: ${SHARED_KEYS} ${SHARED_SCALARS}"
  drift="$(diff \
            <(extract_shared "${GW_CONFIG}") \
            <(extract_shared "${CLI_CONFIG}") 2>/dev/null)"
  if [[ -z "${drift}" ]]; then
    echo "    OK: shared keys are identical (surface-specific keys ignored)"
  else
    echo "    *** DRIFT on SHARED keys: a one-sided edit fails silently. ***"
    echo "    Diff (gateway < , tui/cli >):"
    printf '%s\n' "${drift}" | sed 's/^/      /'
    problems=$((problems+1))
  fi
else
  echo "    ?? one or both config files missing"
fi
hr

echo "[7] Gateway PID/lock symlinks (the two homes must agree)"
for f in gateway.pid gateway.lock; do
  src="${HOME_DIR}/${f}"
  link="${HERMES_DIR}/${f}"
  if [[ -e "${src}" ]]; then
    if [[ -L "${link}" && "$(readlink -f "${link}")" == "$(readlink -f "${src}")" ]]; then
      echo "    OK: ${link} -> ${src}"
    else
      echo "    *** ${link} does not point at ${src} — stop/status will look at the wrong file ***"
      echo "    Fix: sudo ln -sf ${src} ${link}"
      problems=$((problems+1))
    fi
  else
    echo "    note: ${src} absent (gateway may be stopped)"
  fi
done
hr

echo "Deeper checks (not automatable here):"
echo "  [3] provider/model 404s   -> hermes-debug/references/provider-routing.md"
echo "  [4] delegation explosion  -> hermes-debug/references/delegation-explosion.md"
echo "  [5] tool_search deferral, [6] warm-vs-cold, [8] reasoning_effort"
hr
echo "Triage found ${problems} hard problem(s)."
exit 0
