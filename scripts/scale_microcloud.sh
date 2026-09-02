#!/usr/bin/env bash
# scale_microcloud.sh — scale an existing MicroCloud environment
set -euo pipefail

WORKSPACE=""
TARGET_NODES=""
SIZING_TIER=""
NODE_CPU=""
NODE_MEMORY_MB=""
ROOT_DISK_GIB=""
CEPH_DISK_GIB=""
CEPH_DISKS_PER_NODE=""
LOCAL_DISK_GIB=""
AUTO_APPROVE=false
EXPECTED_STATE_LINEAGE=""
EXPECTED_STATE_SERIAL=""
EXPECTED_CURRENT_NODES=""
EXPECTED_TARGET_NODES=""

for arg in "$@"; do
    case "${arg}" in
        --workspace=*) WORKSPACE="${arg#*=}" ;;
        --target-nodes=*) TARGET_NODES="${arg#*=}" ;;
        --sizing-tier=*) SIZING_TIER="${arg#*=}" ;;
        --node-cpu=*) NODE_CPU="${arg#*=}" ;;
        --node-memory-mb=*) NODE_MEMORY_MB="${arg#*=}" ;;
        --root-disk-gib=*) ROOT_DISK_GIB="${arg#*=}" ;;
        --ceph-disk-gib=*) CEPH_DISK_GIB="${arg#*=}" ;;
        --ceph-disks-per-node=*) CEPH_DISKS_PER_NODE="${arg#*=}" ;;
        --local-disk-gib=*) LOCAL_DISK_GIB="${arg#*=}" ;;
        --auto-approve) AUTO_APPROVE=true ;;
        --expected-state-lineage=*) EXPECTED_STATE_LINEAGE="${arg#*=}" ;;
        --expected-state-serial=*) EXPECTED_STATE_SERIAL="${arg#*=}" ;;
        --expected-current-nodes=*) EXPECTED_CURRENT_NODES="${arg#*=}" ;;
        --expected-target-nodes=*) EXPECTED_TARGET_NODES="${arg#*=}" ;;
        *) echo "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

if [[ -z "${WORKSPACE}" ]]; then
    echo "Missing required --workspace"
    exit 1
fi
if [[ -z "${TARGET_NODES}" ]]; then
    echo "Missing required --target-nodes"
    exit 1
fi
if ! [[ "${TARGET_NODES}" =~ ^[0-9]+$ ]]; then
    echo "target-nodes must be an integer"
    exit 1
fi
if (( TARGET_NODES < 3 )); then
    echo "target-nodes must be >= 3"
    exit 1
fi
if [[ -z "${EXPECTED_STATE_LINEAGE}" || -z "${EXPECTED_STATE_SERIAL}" \
      || -z "${EXPECTED_CURRENT_NODES}" || -z "${EXPECTED_TARGET_NODES}" ]]; then
    echo "Missing approval-bound Terraform state identity/count parameters"
    exit 1
fi
if [[ "${TARGET_NODES}" != "${EXPECTED_TARGET_NODES}" ]]; then
    echo "target-nodes does not match the approved target"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"
TF_DIR="${LAB_AI_TERRAFORM_DIR:-${REPO_ROOT}/terraform}"
ADD_SCRIPT="${SCRIPT_DIR}/add_cluster_node.sh"

if [[ ! -f "${ADD_SCRIPT}" ]]; then
    echo "Add-node script not found: ${ADD_SCRIPT}"
    exit 1
fi

if [[ -n "${SIZING_TIER}" || -n "${NODE_CPU}" || -n "${NODE_MEMORY_MB}" \
      || -n "${ROOT_DISK_GIB}" || -n "${CEPH_DISK_GIB}" \
      || -n "${CEPH_DISKS_PER_NODE}" || -n "${LOCAL_DISK_GIB}" ]]; then
    echo "Scale cannot resize existing node or disk geometry. Create a new environment for different sizing."
    exit 1
fi

for tool in tofu lxc; do
    command -v "${tool}" >/dev/null 2>&1 || { echo "${tool} not found"; exit 1; }
done

cd "${TF_DIR}"
tofu init -input=false >/dev/null 2>&1
if ! tofu workspace list | tr -d '* ' | grep -qx "${WORKSPACE}"; then
    echo "Workspace '${WORKSPACE}' not found"
    exit 1
fi

CURRENT_NODES=$(TF_WORKSPACE="${WORKSPACE}" tofu output -json node_names 2>/dev/null \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo "0")
if [[ "${CURRENT_NODES}" -eq 0 ]]; then
    echo "Could not determine current node count for '${WORKSPACE}'"
    exit 1
fi
if [[ "${CURRENT_NODES}" != "${EXPECTED_CURRENT_NODES}" ]]; then
    echo "Current node count changed after approval: expected ${EXPECTED_CURRENT_NODES}, found ${CURRENT_NODES}"
    exit 1
fi

if [[ "${TARGET_NODES}" -eq "${CURRENT_NODES}" ]]; then
    echo "Environment '${WORKSPACE}' already has ${TARGET_NODES} nodes. Nothing to do."
    exit 0
fi

if [[ "${TARGET_NODES}" -lt "${CURRENT_NODES}" ]]; then
    echo "Safe downscale is not implemented. Members and Ceph OSDs must be drained and removed before destroying VMs."
    exit 1
fi

ADD_NODES=$(( TARGET_NODES - CURRENT_NODES ))
CMD=(
    "bash"
    "${ADD_SCRIPT}"
    "--workspace=${WORKSPACE}"
    "--add-nodes=${ADD_NODES}"
    "--expected-state-lineage=${EXPECTED_STATE_LINEAGE}"
    "--expected-state-serial=${EXPECTED_STATE_SERIAL}"
    "--expected-current-nodes=${EXPECTED_CURRENT_NODES}"
    "--expected-target-nodes=${EXPECTED_TARGET_NODES}"
)
if [[ "${AUTO_APPROVE}" == "true" ]]; then
    CMD+=("--auto-approve")
fi

echo "Expanding environment '${WORKSPACE}' from ${CURRENT_NODES} to ${TARGET_NODES} nodes..."
exec "${CMD[@]}"
