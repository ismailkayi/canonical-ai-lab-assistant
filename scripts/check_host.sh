#!/usr/bin/env bash
# Pre-flight checks. Read-only: changes nothing.
source "$(dirname "$0")/common.sh"
TRACE_COMMANDS=no   # keep the pre-flight report short

problems=0
fail() { echo -e "${RED}✘${NC}  $*"; problems=$((problems + 1)); }

title "Host checks"

if [[ -e /dev/kvm ]]; then ok "Hardware virtualization (/dev/kvm) available"
else fail "/dev/kvm missing — this machine cannot run VMs. Enable VT-x/AMD-V or nested virtualization."; fi

nested=$(cat /sys/module/kvm_intel/parameters/nested /sys/module/kvm_amd/parameters/nested 2>/dev/null | head -n1 || true)
if [[ "${INSTANCE_TYPE}" == "vm" ]]; then
    if [[ "${nested}" == "Y" || "${nested}" == "1" ]]; then ok "Nested virtualization enabled (needed for VM instances in step 2)"
    else warn "Nested virtualization not detected — step 2 will fall back to containers. (Set INSTANCE_TYPE=container to silence this.)"; fi
fi

if type -P lxc >/dev/null && lxc info >/dev/null 2>&1; then
    ok "LXD installed and usable by ${USER}"
else
    fail "LXD missing or not usable — run ./demo.py setup (then log out and back in)"
fi

mem_needed_gib=$(( NODE_COUNT * ${NODE_MEMORY%GiB} + 4 ))
mem_total_gib=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
if (( mem_total_gib >= mem_needed_gib )); then ok "Memory: ${mem_total_gib} GiB (need ~${mem_needed_gib} GiB)"
else fail "Memory: ${mem_total_gib} GiB, need ~${mem_needed_gib} GiB for ${NODE_COUNT} × ${NODE_MEMORY}. Lower NODE_COUNT or NODE_MEMORY in demo.conf."; fi
# Total RAM can be enough while desktop apps are using most of it right now.
mem_avail_gib=$(( $(awk '/MemAvailable/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
lab_running=$(lxc list "${NODE_PREFIX}-" -c s -f csv 2>/dev/null | grep -c RUNNING || true)
vm_mem_gib=$(( mem_needed_gib - 4 ))
if (( lab_running == 0 )); then
    if (( mem_avail_gib >= vm_mem_gib + 2 )); then ok "Memory available right now: ${mem_avail_gib} GiB (VMs use up to ${vm_mem_gib} GiB)"
    else warn "Memory available right now: ${mem_avail_gib} GiB, the VMs can use up to ${vm_mem_gib} GiB. Close some apps, or lower NODE_COUNT / NODE_MEMORY."; fi
fi

(( NODE_COUNT >= 3 )) || fail "NODE_COUNT=${NODE_COUNT}: MicroCloud needs at least 3 members."

cpu_total=$(nproc)
if (( cpu_total >= NODE_COUNT )); then ok "CPU: ${cpu_total} threads"
else warn "CPU: ${cpu_total} threads for ${NODE_COUNT} nodes — it will work, but slowly."; fi

disk_needed_gib=$(( NODE_COUNT * (${NODE_ROOT_DISK%GiB} + ${NODE_CEPH_DISK%GiB}) / 2 ))
disk_free_gib=$(df -BG --output=avail "$( [[ -d /var/snap ]] && echo /var/snap || echo / )" 2>/dev/null | tail -n1 | tr -dc '0-9' || echo 0)
if (( disk_free_gib >= disk_needed_gib )); then ok "Disk: ${disk_free_gib} GiB free (need ~${disk_needed_gib} GiB)"
else warn "Disk: ${disk_free_gib} GiB free, ~${disk_needed_gib} GiB recommended."; fi

title "Settings (demo.conf)"
[[ -f "${REPO_ROOT}/demo.conf" ]] || fail "demo.conf not found — run: cp demo.conf.example demo.conf"
if [[ -n "${LANDSCAPE_ACCOUNT_NAME}" || -n "${LANDSCAPE_FQDN}" ]]; then ok "Landscape configured"
else fail "Landscape not configured (LANDSCAPE_ACCOUNT_NAME or LANDSCAPE_FQDN) — needed for step 3"; fi
if [[ -n "${PRO_TOKEN}" ]]; then ok "Ubuntu Pro token set"
else fail "PRO_TOKEN empty — needed for step 4 (USG)"; fi
if [[ -n "${OPENROUTER_API_KEY}" ]]; then ok "OpenRouter key set (model: ${OPENROUTER_MODEL})"
else warn "OPENROUTER_API_KEY empty — the demo uses built-in narration instead of the AI"; fi

echo
if (( problems > 0 )); then
    die "${problems} problem(s) found. Fix them before the demo."
fi
ok "All checks passed."
