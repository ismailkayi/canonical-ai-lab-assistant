#!/usr/bin/env bash
# Report Canonical AI Lab Assistant LXD projects that are not represented in
# Terraform state. This command is intentionally read-only.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../terraform"

for tool in lxc tofu; do
    command -v "${tool}" >/dev/null 2>&1 || {
        echo "${tool} not found" >&2
        exit 1
    }
done

cd "${TF_DIR}"
tofu init -input=false >/dev/null 2>&1 || true

mapfile -t WORKSPACES < <(
    tofu workspace list 2>/dev/null |
        sed 's/*//g' |
        awk '{$1=$1; print}' |
        grep -v '^$' || true
)

workspace_has_project() {
    local workspace="$1"
    local project="$2"
    local state_project=""

    state_project=$(
        TF_WORKSPACE="${workspace}" tofu output -json deployment_spec 2>/dev/null |
            python3 -c 'import json,sys; print(json.load(sys.stdin).get("lxd_project_name", ""))' \
            2>/dev/null || true
    )
    [[ "${state_project}" == "${project}" ]] || return 1
    TF_WORKSPACE="${workspace}" tofu state list 2>/dev/null |
        grep -qx 'lxd_project.environment'
}

printf '%-10s %-44s %-32s %-44s %s\n' \
    "Type" "Resource" "Workspace" "LXD Project" "Status"
printf '%-10s %-44s %-32s %-44s %s\n' \
    "----------" \
    "--------------------------------------------" \
    "--------------------------------" \
    "--------------------------------------------" \
    "------------"

found=0
while IFS= read -r project; do
    [[ -z "${project}" ]] && continue
    manager=$(lxc project get "${project}" \
        user.canonical-ai-lab-assistant.managed-by 2>/dev/null || true)
    [[ "${manager}" == "canonical-ai-lab-assistant" ]] || continue

    workspace=$(lxc project get "${project}" \
        user.canonical-ai-lab-assistant.workspace 2>/dev/null || true)
    status="ORPHAN"
    for candidate in "${WORKSPACES[@]}"; do
        if [[ "${candidate}" == "${workspace}" ]] \
                && workspace_has_project "${candidate}" "${project}"; then
            status="managed"
            break
        fi
    done

    printf '%-10s %-44s %-32s %-44s %s\n' \
        "project" "${project}" "${workspace:-unknown}" "${project}" "${status}"
    found=$((found + 1))
done < <(lxc project list --format csv -c n)

while IFS= read -r network; do
    [[ -z "${network}" ]] && continue
    workspace=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.owner 2>/dev/null || true)
    project=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.project 2>/dev/null || true)
    if [[ -z "${workspace}" || -z "${project}" ]]; then
        if [[ "${network}" =~ ^mc-.*-up$ || "${network}" =~ ^ca-[0-9a-f]{8}-(mg|up|ov|ce)$ ]]; then
            printf '%-10s %-44s %-32s %-44s %s\n' \
                "network" "${network}" "unknown" "default" "UNOWNED"
            found=$((found + 1))
        fi
        continue
    fi

    status="ORPHAN"
    for candidate in "${WORKSPACES[@]}"; do
        if [[ "${candidate}" == "${workspace}" ]] \
                && workspace_has_project "${candidate}" "${project}"; then
            status="managed"
            break
        fi
    done
    printf '%-10s %-44s %-32s %-44s %s\n' \
        "network" "${network}" "${workspace}" "${project}" "${status}"
    found=$((found + 1))
done < <(lxc --project default network list --format csv -c n)

while IFS= read -r volume; do
    [[ -z "${volume}" ]] && continue
    [[ "${volume}" =~ -microcloud-(ceph|local)- ]] || continue
    owner=$(lxc --project default storage volume get default "${volume}" \
        user.canonical-ai-lab-assistant.owner 2>/dev/null || true)
    [[ -z "${owner}" ]] || continue
    printf '%-10s %-44s %-32s %-44s %s\n' \
        "volume" "${volume}" "unknown" "default" "UNOWNED"
    found=$((found + 1))
done < <(
    lxc --project default storage volume list default --format csv 2>/dev/null |
        awk -F',' '$1 == "custom" {print $2}'
)

if [[ "${found}" -eq 0 ]]; then
    echo "No Canonical AI Lab Assistant projects or tagged networks found."
fi
