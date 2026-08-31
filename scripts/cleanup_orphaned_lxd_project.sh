#!/usr/bin/env bash
# Delete one assistant-owned LXD project that has no matching Terraform state.
# Untagged resources and managed projects are always refused.
set -euo pipefail

PROJECT=""
WORKSPACE=""
AUTO_APPROVE=false

for arg in "$@"; do
    case "${arg}" in
        --project=*) PROJECT="${arg#*=}" ;;
        --workspace=*) WORKSPACE="${arg#*=}" ;;
        --auto-approve) AUTO_APPROVE=true ;;
        *) echo "Unknown argument: ${arg}" >&2; exit 1 ;;
    esac
done

if [[ -z "${PROJECT}" || -z "${WORKSPACE}" ]]; then
    echo "Usage: $0 --project=<name> --workspace=<name>" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../terraform"

for tool in lxc tofu flock; do
    command -v "${tool}" >/dev/null 2>&1 || {
        echo "${tool} not found" >&2
        exit 1
    }
done

LOCK_ROOT="${SNAP_USER_COMMON:-${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}}"
mkdir -p "${LOCK_ROOT}"
LOCK_FILE="${LOCK_ROOT}/canonical-ai-lab-assistant-terraform-${UID}.lock"
if [[ -n "${LAB_AI_TERRAFORM_LOCK_FD:-}" \
      && -e "/proc/$$/fd/${LAB_AI_TERRAFORM_LOCK_FD}" ]]; then
    :
else
    exec 9>"${LOCK_FILE}"
    flock -x 9
fi

PROJECT_EXISTS=false
assert_project_ownership() {
    local manager=""
    local owner=""
    manager=$(lxc project get "${PROJECT}" \
        user.canonical-ai-lab-assistant.managed-by 2>/dev/null || true)
    owner=$(lxc project get "${PROJECT}" \
        user.canonical-ai-lab-assistant.workspace 2>/dev/null || true)
    if [[ "${manager}" != "canonical-ai-lab-assistant" \
            || "${owner}" != "${WORKSPACE}" ]]; then
        echo "Refusing orphan cleanup: project ownership mismatch" >&2
        echo "project=${PROJECT}, managed-by=${manager:-unset}, workspace=${owner:-unset}" >&2
        exit 1
    fi
}

if lxc project show "${PROJECT}" >/dev/null 2>&1; then
    PROJECT_EXISTS=true
    assert_project_ownership
fi

cd "${TF_DIR}"
if ! tofu init -input=false >/dev/null 2>&1; then
    echo "Refusing orphan cleanup: Terraform initialization failed" >&2
    exit 1
fi
set +e
WORKSPACE_OUTPUT=$(tofu workspace list 2>&1)
WORKSPACE_RC=$?
set -e
if [[ "${WORKSPACE_RC}" -ne 0 ]]; then
    echo "Refusing orphan cleanup: Terraform workspaces cannot be enumerated" >&2
    echo "${WORKSPACE_OUTPUT}" >&2
    exit 1
fi

while IFS= read -r candidate; do
    [[ -z "${candidate}" ]] && continue
    set +e
    STATE_LIST=$(TF_WORKSPACE="${candidate}" tofu state list 2>&1)
    STATE_RC=$?
    set -e
    if [[ "${STATE_RC}" -ne 0 ]]; then
        if [[ "${STATE_LIST}" == *"No state file was found"* ]]; then
            continue
        fi
        echo "Refusing orphan cleanup: Terraform state for '${candidate}' is unreadable" >&2
        echo "${STATE_LIST}" >&2
        exit 1
    fi
    if [[ -z "${STATE_LIST//[[:space:]]/}" ]]; then
        continue
    fi

    set +e
    PROJECT_STATE=$(TF_WORKSPACE="${candidate}" tofu state pull 2>&1)
    PROJECT_STATE_RC=$?
    set -e
    if [[ "${PROJECT_STATE_RC}" -ne 0 ]]; then
        echo "Refusing orphan cleanup: project state in '${candidate}' is unreadable" >&2
        echo "${PROJECT_STATE}" >&2
        exit 1
    fi
    state_references=$(
        python3 -c 'import json,sys
state=json.load(sys.stdin)
target=sys.argv[1]
for resource in state.get("resources", []):
    for instance in resource.get("instances", []):
        attrs=instance.get("attributes", {})
        if attrs.get("project") == target or (
            resource.get("type") == "lxd_project" and attrs.get("name") == target
        ):
            index=instance.get("index_key")
            suffix=f"[{index}]" if "index_key" in instance else ""
            print("{}.{}{}".format(resource.get("type"), resource.get("name"), suffix))
' "${PROJECT}" <<< "${PROJECT_STATE}"
    )
    if [[ -n "${state_references}" ]]; then
        echo "Refusing orphan cleanup: project is referenced by Terraform workspace '${candidate}'" >&2
        echo "${state_references}" >&2
        exit 1
    fi
done < <(
    printf '%s\n' "${WORKSPACE_OUTPUT}" |
        sed 's/*//g' |
        awk '{$1=$1; print}' |
        grep -v '^$'
)

NETWORKS=()
while IFS= read -r network; do
    [[ -z "${network}" ]] && continue
    network_project=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.project 2>/dev/null || true)
    [[ "${network_project}" == "${PROJECT}" ]] || continue
    network_owner=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.owner 2>/dev/null || true)
    if [[ "${network_owner}" != "${WORKSPACE}" ]]; then
        echo "Refusing orphan cleanup: network '${network}' owner mismatch" >&2
        exit 1
    fi
    NETWORKS+=("${network}")
done < <(lxc --project default network list --format csv -c n)

if [[ "${PROJECT_EXISTS}" != "true" && "${#NETWORKS[@]}" -eq 0 ]]; then
    echo "Refusing orphan cleanup: no owned project or tagged networks were found" >&2
    exit 1
fi

echo "Orphan cleanup plan"
echo "  Workspace  : ${WORKSPACE}"
echo "  LXD project: ${PROJECT}"
echo "  Networks   : ${NETWORKS[*]:-(none)}"
if [[ "${AUTO_APPROVE}" != "true" ]]; then
    read -r -p "Delete this owned orphan project and its tagged networks? [y/N] " answer
    [[ "${answer,,}" == "y" ]] || {
        echo "Cancelled."
        exit 0
    }
fi

CURRENT_NETWORKS=()
while IFS= read -r network; do
    [[ -z "${network}" ]] && continue
    network_project=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.project 2>/dev/null || true)
    [[ "${network_project}" == "${PROJECT}" ]] || continue
    network_owner=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.owner 2>/dev/null || true)
    if [[ "${network_owner}" != "${WORKSPACE}" ]]; then
        echo "Refusing orphan cleanup: network '${network}' owner changed" >&2
        exit 1
    fi
    CURRENT_NETWORKS+=("${network}")
done < <(lxc --project default network list --format csv -c n)

original_networks=$(printf '%s\n' "${NETWORKS[@]}" | sort)
current_networks=$(printf '%s\n' "${CURRENT_NETWORKS[@]}" | sort)
if [[ "${original_networks}" != "${current_networks}" ]]; then
    echo "Refusing orphan cleanup: tagged network set changed after inspection" >&2
    exit 1
fi

if [[ "${PROJECT_EXISTS}" == "true" ]]; then
    if ! lxc project show "${PROJECT}" >/dev/null 2>&1; then
        echo "Refusing orphan cleanup: project disappeared after inspection" >&2
        exit 1
    fi
    assert_project_ownership
    lxc project delete --force "${PROJECT}"
elif lxc project show "${PROJECT}" >/dev/null 2>&1; then
    echo "Refusing orphan cleanup: project appeared after inspection" >&2
    exit 1
fi
for network in "${NETWORKS[@]}"; do
    network_owner=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.owner 2>/dev/null || true)
    network_project=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.project 2>/dev/null || true)
    if [[ "${network_owner}" != "${WORKSPACE}" || "${network_project}" != "${PROJECT}" ]]; then
        echo "Refusing orphan cleanup: network '${network}' ownership changed before deletion" >&2
        exit 1
    fi
    lxc --project default network delete "${network}"
done

echo "Deleted owned orphan project '${PROJECT}' and ${#NETWORKS[@]} tagged network(s)."
