#!/usr/bin/env bash
# list_microcloud_environments.sh — list managed MicroCloud environments
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"
TF_DIR="${REPO_ROOT}/terraform"

if [[ ! -d "${TF_DIR}" ]]; then
    echo "Terraform directory not found: ${TF_DIR}"
    exit 1
fi

if ! command -v tofu >/dev/null 2>&1; then
    echo "OpenTofu (tofu) not found"
    exit 1
fi

if ! command -v lxc >/dev/null 2>&1; then
    echo "lxc not found"
    exit 1
fi

cd "${TF_DIR}"
tofu init -input=false >/dev/null 2>&1 || true

mapfile -t WORKSPACES < <(
    tofu workspace list 2>/dev/null \
        | sed 's/*//g' \
        | awk '{$1=$1;print}' \
        | grep -v '^default$' \
        | grep -v '^$' \
        || true
)

if [[ ${#WORKSPACES[@]} -eq 0 ]]; then
    echo "No managed MicroCloud environments found."
    exit 0
fi

# Check if any workspace has nodes; count active workspaces.
active_count=0
results=()

for ws in "${WORKSPACES[@]}"; do
    [[ -z "${ws}" ]] && continue
    prefix="${ws//_/-}-node-"
    deployment_spec=$(TF_WORKSPACE="${ws}" tofu output -json deployment_spec 2>/dev/null || true)
    [[ -z "${deployment_spec}" || "${deployment_spec}" == "null" ]] && continue
    readarray -t spec_values < <(
        python3 -c 'import json,sys
data=json.load(sys.stdin)
print(data.get("lxd_project_name", ""))
print(data.get("network_mode", "standard-2nic"))' <<< "${deployment_spec}"
    )
    lxd_project="${spec_values[0]:-}"
    network_mode="${spec_values[1]:-standard-2nic}"
    [[ -z "${lxd_project}" ]] && continue

    nodes=$( (lxc --project "${lxd_project}" list --format csv -c n 2>/dev/null \
        | grep -E "^${prefix}[0-9]+$" || true) | wc -l | tr -d ' ')
    running=$(lxc --project "${lxd_project}" list --format csv -c ns 2>/dev/null \
        | awk -F',' -v pfx="${prefix}" '$1 ~ ("^" pfx "[0-9]+$") && $2 == "RUNNING" {c++} END {print c+0}')

    # Skip workspaces with zero nodes (destroyed but workspace not yet deleted)
    if [[ "${nodes}" -eq 0 ]]; then
        continue
    fi

    results+=("${ws}|${nodes}|${running}|${network_mode}|${lxd_project}|${prefix}")
    active_count=$((active_count + 1))
done

if [[ ${active_count} -eq 0 ]]; then
    echo "No active MicroCloud environments found. (All workspaces are empty.)"
    exit 0
fi

printf '%-24s %-8s %-10s %-25s %-40s %s\n' "Workspace" "Nodes" "Running" "Network Mode" "LXD Project" "Node Prefix"
printf '%-24s %-8s %-10s %-25s %-40s %s\n' "------------------------" "--------" "----------" "-------------------------" "----------------------------------------" "--------------------------"

for entry in "${results[@]}"; do
    IFS='|' read -r ws nodes running network_mode lxd_project prefix <<< "${entry}"
    printf '%-24s %-8s %-10s %-25s %-40s %s\n' \
        "${ws}" "${nodes}" "${running}" "${network_mode}" "${lxd_project}" "${prefix}"
done
