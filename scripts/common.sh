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

# Print a command, then run it — so the audience sees exactly what happens.
show() { echo -e "${BOLD}\$ $*${NC}"; "$@"; }

# Hands-on mode: every step script accepts --print, which prints the commands
# to type by hand (read from stdin) instead of running anything.
# Comment lines are dimmed and commands are bold on a terminal; when piped or
# redirected to a file the text stays plain, ready to copy.
print_guide() {
    local line
    if [[ -t 1 ]]; then
        while IFS= read -r line; do
            case "${line}" in
                "#"*) printf "${DIM}%s${NC}\n" "${line}" ;;
                "")   echo ;;
                *)    printf "${BOLD}%s${NC}\n" "${line}" ;;
            esac
        done
    else
        cat
    fi
}

# --- MicroCloud helpers ----------------------------------------------------
node_name()     { echo "${NODE_PREFIX}-$1"; }
instance_name() { echo "${INSTANCE_PREFIX}-$1"; }

# Run a command inside MicroCloud (on the first cluster member).
mc() { lxc exec "${LEADER}" -- "$@"; }

# Run a command inside a workload instance (instances live inside MicroCloud).
in_instance() { local name="$1"; shift; mc lxc exec "${name}" -- "$@"; }

# Same, but print it as the audience would type it inside the instance.
show_in() { local name="$1"; shift; echo -e "${BOLD}${name}\$ $*${NC}"; in_instance "${name}" "$@"; }

require_lxd() {
    command -v lxc >/dev/null || die "LXD is not installed. Run: ./demo.py setup"
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
    until "$@" >/dev/null 2>&1; do
        (( SECONDS - start > timeout )) && die "Timed out waiting for ${what}"
        sleep 3
    done
}
