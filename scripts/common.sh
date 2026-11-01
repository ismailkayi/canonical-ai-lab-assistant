#!/usr/bin/env bash
# Shared settings and helpers for every demo step. Sourced, not executed.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${REPO_ROOT}/logs"
REPORT_DIR="${REPO_ROOT}/reports"
mkdir -p "${LOG_DIR}" "${REPORT_DIR}"

# Load settings: demo.conf overrides the example defaults.
# shellcheck disable=SC1091
source "${REPO_ROOT}/demo.conf.example"
if [[ -f "${REPO_ROOT}/demo.conf" ]]; then
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/demo.conf"
fi

NODE_PREFIX="mc-node"            # host VMs: mc-node-1 .. mc-node-<NODE_COUNT>
INSTANCE_PREFIX="mc-instance"    # workloads: mc-instance-1 .. mc-instance-<INSTANCE_COUNT>
UPLINK_BRIDGE="mcbr0"            # host bridge that gives MicroCloud's OVN network internet access
LEADER="${NODE_PREFIX}-1"

# --- output helpers --------------------------------------------------------
BOLD='\033[1m'; DIM='\033[2m'; BLUE='\033[0;34m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

info()  { echo -e "${BLUE}==>${NC} $*"; }
ok()    { echo -e "${GREEN}✔${NC}  $*"; }
warn()  { echo -e "${YELLOW}!${NC}  $*"; }
die()   { echo -e "${RED}✘  $*${NC}" >&2; exit 1; }
title() { echo; echo -e "${BOLD}$*${NC}"; }

# --- Command trace ----------------------------------------------------------
# Every lxc command is printed before it runs, so you can see (and learn) what
# each step really does:
#   [host]$ ...                       runs on this machine
#   [mc-node-1]$ ...                  runs inside a cluster member
#   [mc-node-1 → mc-instance-1]$ ...  runs inside an instance on the cluster
# Background commands are dim; the key commands of each step are bold.
# Secrets from demo.conf are printed as <hidden>. TRACE_COMMANDS=no turns the
# dim ones off.
# The trace goes to file descriptor 3 (the terminal), so it stays visible
# even for work whose output is sent to a log file.
exec 3>&1

_trace_cmd() {
    [[ "${TRACE_BOLD:-}" == 1 || ( "${TRACE_COMMANDS:-yes}" == yes && -z "${TRACE_QUIET:-}" ) ]] || return 0
    local -a cmd=("$@")
    local where="" line="" arg secret style="${DIM}"
    while [[ "${cmd[0]:-}" == lxc && "${cmd[1]:-}" == exec && "${cmd[3]:-}" == "--" ]]; do
        where+="${where:+ → }${cmd[2]}"
        cmd=("${cmd[@]:4}")
    done
    for arg in "${cmd[@]}"; do
        if [[ "${arg}" =~ ^[][A-Za-z0-9_./:=,@%+*^-]+$ ]]; then line+=" ${arg}"
        elif [[ "${arg}" != *"'"* ]]; then line+=" '${arg}'"
        else line+=" $(printf '%q' "${arg}")"; fi
    done
    for secret in "${PRO_TOKEN:-}" "${LANDSCAPE_REGISTRATION_KEY:-}"; do
        [[ -z "${secret}" ]] || line="${line//"${secret}"/<hidden>}"
    done
    [[ "${TRACE_BOLD:-}" != 1 ]] || style="${BOLD}"
    printf "${style}[%s]\$%s${NC}${TRACE_NOTE:+${DIM}  # ${TRACE_NOTE}${NC}}\n" "${where:-host}" "${line}" >&3
}

# Every lxc call goes through here (the real binary is "command lxc").
lxc() { _trace_cmd lxc "$@"; command lxc "$@"; }

# Print a key command in bold, then run it.
show() {
    if [[ "$1" == lxc ]]; then TRACE_BOLD=1 "$@"
    else TRACE_BOLD=1 _trace_cmd "$@"; "$@"; fi
}

# --- MicroCloud helpers ----------------------------------------------------
node_name()     { echo "${NODE_PREFIX}-$1"; }
instance_name() { echo "${INSTANCE_PREFIX}-$1"; }

# Run a command inside MicroCloud (on the first cluster member).
mc() { lxc exec "${LEADER}" -- "$@"; }

# Run a command inside a workload instance (instances live inside MicroCloud).
in_instance() { local name="$1"; shift; mc lxc exec "${name}" -- "$@"; }

# Same, but print it as the audience would type it inside the instance.
show_in() { local name="$1"; shift; TRACE_BOLD=1 in_instance "${name}" "$@"; }

require_lxd() {
    type -P lxc >/dev/null || die "LXD is not installed. Run: ./demo.py setup"
    lxc info >/dev/null 2>&1 || die "Cannot talk to LXD. Run './demo.py setup', then log out and back in."
}

require_cluster() {
    require_lxd
    lxc info "${LEADER}" >/dev/null 2>&1 || die "MicroCloud is not built yet. Run step 1 first."
    mc lxc cluster list >/dev/null 2>&1 || die "MicroCloud is not running on ${LEADER}. Run step 1 first."
}

# Wait until a command succeeds, or fail after a timeout (seconds).
wait_for() {
    local timeout="$1" what="$2"; shift 2
    local start=$SECONDS
    TRACE_NOTE="repeated until it succeeds" _trace_cmd "$@"
    local TRACE_QUIET=1   # do not print every retry
    until "$@" >/dev/null 2>&1; do
        (( SECONDS - start > timeout )) && die "Timed out waiting for ${what}"
        sleep 3
    done
}
