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
    nodes=$( (lxc list --format csv -c n 2>/dev/null | grep -E "^${prefix}[0-9]+$" || true) | wc -l | tr -d ' ')
    running=$(lxc list --format csv -c ns 2>/dev/null | awk -F',' -v pfx="${prefix}" '$1 ~ ("^" pfx "[0-9]+$") && $2 == "RUNNING" {c++} END {print c+0}')
    network_mode=$(
        TF_WORKSPACE="${ws}" tofu output -json deployment_spec 2>/dev/null \
            | python3 -c 'import json,sys; print(json.load(sys.stdin).get("network_mode", "standard-2nic"))' \
            2>/dev/null || echo "standard-2nic"
    )

    # Skip workspaces with zero nodes (destroyed but workspace not yet deleted)
    if [[ "${nodes}" -eq 0 ]]; then
        continue
    fi

    results+=("${ws}|${nodes}|${running}|${network_mode}|${prefix}")
    active_count=$((active_count + 1))
done

if [[ ${active_count} -eq 0 ]]; then
    echo "No active MicroCloud environments found. (All workspaces are empty.)"
    exit 0
fi

printf '%-24s %-8s %-10s %-25s %s\n' "Workspace" "Nodes" "Running" "Network Mode" "Node Prefix"
printf '%-24s %-8s %-10s %-25s %s\n' "------------------------" "--------" "----------" "-------------------------" "--------------------------"

for entry in "${results[@]}"; do
    IFS='|' read -r ws nodes running network_mode prefix <<< "${entry}"
    printf '%-24s %-8s %-10s %-25s %s\n' "${ws}" "${nodes}" "${running}" "${network_mode}" "${prefix}"
done
