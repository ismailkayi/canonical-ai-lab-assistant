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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="${SCRIPT_DIR}/deploy_microcloud.sh"

if [[ ! -x "${DEPLOY_SCRIPT}" ]]; then
    echo "Deploy script not executable: ${DEPLOY_SCRIPT}"
    exit 1
fi

# Convert workspace name to deploy script user-prefix convention.
# Example: lab_microcloud -> lab
if [[ "${WORKSPACE}" == *_microcloud ]]; then
    USER_PREFIX="${WORKSPACE%_microcloud}"
else
    USER_PREFIX="${WORKSPACE}"
fi

CMD=("bash" "${DEPLOY_SCRIPT}" "--user-prefix=${USER_PREFIX}" "--nodes=${TARGET_NODES}")

if [[ -n "${SIZING_TIER}" ]]; then
    CMD+=("--sizing-tier=${SIZING_TIER}")
fi
if [[ -n "${NODE_CPU}" ]]; then
    CMD+=("--node-cpu=${NODE_CPU}")
fi
if [[ -n "${NODE_MEMORY_MB}" ]]; then
    CMD+=("--node-memory-mb=${NODE_MEMORY_MB}")
fi
if [[ -n "${ROOT_DISK_GIB}" ]]; then
    CMD+=("--root-disk-gib=${ROOT_DISK_GIB}")
fi
if [[ -n "${CEPH_DISK_GIB}" ]]; then
    CMD+=("--ceph-disk-gib=${CEPH_DISK_GIB}")
fi
if [[ -n "${CEPH_DISKS_PER_NODE}" ]]; then
    CMD+=("--ceph-disks-per-node=${CEPH_DISKS_PER_NODE}")
fi
if [[ -n "${LOCAL_DISK_GIB}" ]]; then
    CMD+=("--local-disk-gib=${LOCAL_DISK_GIB}")
fi
if [[ "${AUTO_APPROVE}" == "true" ]]; then
    CMD+=("--auto-approve")
fi

echo "Scaling environment '${WORKSPACE}' to ${TARGET_NODES} nodes..."
exec "${CMD[@]}"
