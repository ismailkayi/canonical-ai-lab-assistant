#!/usr/bin/env bash
# add_cluster_node.sh — Add one or more nodes to an existing MicroCloud cluster
#
# Flow:
#   1. Determine current node count from Terraform state
#   2. Provision new VMs via tofu apply (increment node count)
#   3. Prepare new nodes (install snaps, detect resources) via Ansible
#   4. Run `microcloud preseed` on joiners + initiator to expand the cluster
#
# Usage:
#   add_cluster_node.sh --workspace=<name> --add-nodes=<N>
#                       [--ceph-disk-gib=<gib>] [--ceph-disks-per-node=<n>]
#                       [--local-disk-gib=<gib>] [--sizing-tier=<tier>]
#                       [--node-cpu=<n>] [--node-memory-mb=<mb>]
#                       [--root-disk-gib=<gib>] [--auto-approve]

set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# -----------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------
WORKSPACE=""
ADD_NODES=1
SIZING_TIER=""
NODE_CPU=""
NODE_MEMORY_MB=""
ROOT_DISK_GIB=""
CEPH_DISK_GIB=""
CEPH_DISKS_PER_NODE=""
LOCAL_DISK_GIB=""
SSH_KEY_PATH="$HOME/.ssh/id_rsa_lab"
EXPECTED_STATE_LINEAGE=""
EXPECTED_STATE_SERIAL=""
EXPECTED_CURRENT_NODES=""
EXPECTED_TARGET_NODES=""

# -----------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------
for arg in "$@"; do
    case "${arg}" in
        --workspace=*)          WORKSPACE="${arg#*=}" ;;
        --add-nodes=*)          ADD_NODES="${arg#*=}" ;;
        --sizing-tier=*)        SIZING_TIER="${arg#*=}" ;;
        --node-cpu=*)           NODE_CPU="${arg#*=}" ;;
        --node-memory-mb=*)     NODE_MEMORY_MB="${arg#*=}" ;;
        --root-disk-gib=*)      ROOT_DISK_GIB="${arg#*=}" ;;
        --ceph-disk-gib=*)      CEPH_DISK_GIB="${arg#*=}" ;;
        --ceph-disks-per-node=*) CEPH_DISKS_PER_NODE="${arg#*=}" ;;
        --local-disk-gib=*)     LOCAL_DISK_GIB="${arg#*=}" ;;
        --ssh-key=*)            SSH_KEY_PATH="${arg#*=}" ;;
        --expected-state-lineage=*) EXPECTED_STATE_LINEAGE="${arg#*=}" ;;
        --expected-state-serial=*) EXPECTED_STATE_SERIAL="${arg#*=}" ;;
        --expected-current-nodes=*) EXPECTED_CURRENT_NODES="${arg#*=}" ;;
        --expected-target-nodes=*) EXPECTED_TARGET_NODES="${arg#*=}" ;;
        --auto-approve)         : ;;  # accepted but no-op (always auto)
        *) log_error "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

if [[ -z "${WORKSPACE}" ]]; then
    log_error "Usage: $0 --workspace=<name> --add-nodes=<N>"
    exit 1
fi
if ! [[ "${ADD_NODES}" =~ ^[1-9][0-9]*$ ]]; then
    log_error "add-nodes must be a positive integer"
    exit 1
fi
if [[ -z "${EXPECTED_STATE_LINEAGE}" || -z "${EXPECTED_STATE_SERIAL}" \
            || -z "${EXPECTED_CURRENT_NODES}" || -z "${EXPECTED_TARGET_NODES}" ]]; then
        log_error "Missing approval-bound Terraform state identity/count parameters"
        exit 1
fi

# -----------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/terraform"
PLAYBOOKS_DIR="${REPO_ROOT}/playbooks"

LXD_PREFIX="${WORKSPACE//_/-}"
INITIATOR_NODE="${LXD_PREFIX}-node-1"

# -----------------------------------------------------------------------
# Tool checks
# -----------------------------------------------------------------------
for tool in tofu ansible lxc flock; do
    command -v "${tool}" &>/dev/null || { log_error "${tool} not found"; exit 1; }
done

LOCK_ROOT="${SNAP_USER_COMMON:-${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}}"
mkdir -p "${LOCK_ROOT}"
TERRAFORM_LOCK_FILE="${LOCK_ROOT}/canonical-ai-lab-assistant-terraform-${UID}.lock"
if [[ -n "${LAB_AI_TERRAFORM_LOCK_FD:-}" \
      && -e "/proc/$$/fd/${LAB_AI_TERRAFORM_LOCK_FD}" ]]; then
    log_info "Using inherited infrastructure operation lock"
else
    exec 9>"${TERRAFORM_LOCK_FILE}"
    log_info "Waiting for exclusive infrastructure operation lock..."
    flock -x 9
fi

# -----------------------------------------------------------------------
# Verify workspace exists
# -----------------------------------------------------------------------
cd "${TERRAFORM_DIR}"
tofu init -input=false >/dev/null 2>&1

if ! tofu workspace list | tr -d '* ' | grep -qx "${WORKSPACE}"; then
    log_error "Workspace '${WORKSPACE}' not found. Deploy first with deploy_microcloud.sh."
    exit 1
fi
tofu workspace select "${WORKSPACE}" >/dev/null 2>&1

STATE_IDENTITY=$(tofu state pull)
ACTUAL_STATE_LINEAGE=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["lineage"])' <<< "${STATE_IDENTITY}")
ACTUAL_STATE_SERIAL=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["serial"])' <<< "${STATE_IDENTITY}")
if [[ "${ACTUAL_STATE_LINEAGE}" != "${EXPECTED_STATE_LINEAGE}" \
            || "${ACTUAL_STATE_SERIAL}" != "${EXPECTED_STATE_SERIAL}" ]]; then
        log_error "Terraform state changed after approval; prepare and approve a new plan"
        exit 1
fi

# Read the exact geometry saved by the original deployment. Lifecycle operations
# must never reconstruct existing topology from defaults because that can replace
# disks or resize all current nodes during the count change.
DEPLOYMENT_SPEC_JSON=$(tofu output -json deployment_spec 2>/dev/null || true)
if [[ -z "${DEPLOYMENT_SPEC_JSON}" || "${DEPLOYMENT_SPEC_JSON}" == "null" ]]; then
    log_error "Workspace '${WORKSPACE}' predates versioned deployment specs."
    log_error "Back up anything needed, delete it, then create it fresh with the current version before adding nodes."
    exit 1
fi

spec_value() {
    local key="$1"
    python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' \
        "${key}" <<< "${DEPLOYMENT_SPEC_JSON}"
}

spec_value_optional() {
    local key="$1"
    python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' \
        "${key}" <<< "${DEPLOYMENT_SPEC_JSON}"
}

SPEC_USER_PREFIX=$(spec_value user_prefix)
SPEC_UBUNTU_IMAGE=$(spec_value ubuntu_image)
SPEC_LXD_PROJECT=$(spec_value lxd_project_name)
SPEC_MANAGEMENT_NETWORK=$(spec_value management_network)
SPEC_OVN_UPLINK_NETWORK=$(spec_value ovn_uplink_network)
SPEC_OVN_UNDERLAY_NETWORK=$(spec_value ovn_underlay_network)
SPEC_CEPH_NETWORK=$(spec_value ceph_network)
SPEC_LXD_POOL=$(spec_value lxd_storage_pool)
SPEC_SSH_PUBLIC_KEY=$(spec_value_optional ssh_public_key)
SPEC_NODE_COUNT=$(spec_value node_count)
SPEC_NODE_CPU=$(spec_value node_cpu)
SPEC_NODE_MEMORY_MB=$(spec_value node_memory_mb)
SPEC_ROOT_DISK_GIB=$(spec_value root_disk_gib)
SPEC_CEPH_DISK_GIB=$(spec_value ceph_disk_gib)
SPEC_CEPH_DISKS_PER_NODE=$(spec_value ceph_disks_per_node)
SPEC_LOCAL_DISK_GIB=$(spec_value local_disk_gib)
SPEC_NETWORK_MODE=$(spec_value_optional network_mode)
SPEC_OVN_UNDERLAY_CIDR=$(spec_value_optional ovn_underlay_cidr)
SPEC_CEPH_NETWORK_CIDR=$(spec_value_optional ceph_network_cidr)

[[ -z "${SPEC_NETWORK_MODE}" ]] && SPEC_NETWORK_MODE="standard-2nic"
if [[ "${SPEC_NETWORK_MODE}" == "fully-segregated-4nic" \
        && ( -z "${SPEC_OVN_UNDERLAY_CIDR}" || -z "${SPEC_CEPH_NETWORK_CIDR}" ) ]]; then
    log_error "Saved fully segregated deployment is missing its network CIDRs"
    exit 1
fi

PROJECT_MANAGER=$(lxc project get "${SPEC_LXD_PROJECT}" \
    user.canonical-ai-lab-assistant.managed-by 2>/dev/null || true)
PROJECT_WORKSPACE=$(lxc project get "${SPEC_LXD_PROJECT}" \
    user.canonical-ai-lab-assistant.workspace 2>/dev/null || true)
if [[ "${PROJECT_MANAGER}" != "canonical-ai-lab-assistant" \
        || "${PROJECT_WORKSPACE}" != "${WORKSPACE}" ]]; then
    log_error "LXD project ownership does not match the saved deployment"
    log_error "project=${SPEC_LXD_PROJECT}, managed-by=${PROJECT_MANAGER:-unset}, workspace=${PROJECT_WORKSPACE:-unset}"
    exit 1
fi

validate_owned_network() {
    local network="$1"
    local role="$2"
    local expected_cidr="${3:-}"
    local owner=""
    local project=""
    local actual_role=""
    local actual_cidr=""

    if ! lxc --project default network show "${network}" >/dev/null 2>&1; then
        log_error "Saved ${role} network '${network}' is missing"
        exit 1
    fi
    owner=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.owner 2>/dev/null || true)
    project=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.project 2>/dev/null || true)
    actual_role=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.role 2>/dev/null || true)
    actual_cidr=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.cidr 2>/dev/null || true)
    if [[ "${owner}" != "${WORKSPACE}" || "${project}" != "${SPEC_LXD_PROJECT}" \
            || "${actual_role}" != "${role}" ]]; then
        log_error "Network '${network}' ownership/role does not match the saved deployment"
        exit 1
    fi
    if [[ -n "${expected_cidr}" && "${actual_cidr}" != "${expected_cidr}" ]]; then
        log_error "Network '${network}' CIDR metadata is ${actual_cidr:-unset}; expected ${expected_cidr}"
        exit 1
    fi
}

validate_owned_network "${SPEC_MANAGEMENT_NETWORK}" "management"
validate_owned_network "${SPEC_OVN_UPLINK_NETWORK}" "ovn-uplink"
if [[ "${SPEC_NETWORK_MODE}" == "fully-segregated-4nic" ]]; then
    validate_owned_network \
        "${SPEC_OVN_UNDERLAY_NETWORK}" "ovn-underlay" "${SPEC_OVN_UNDERLAY_CIDR}"
    validate_owned_network \
        "${SPEC_CEPH_NETWORK}" "ceph" "${SPEC_CEPH_NETWORK_CIDR}"
fi

if [[ -z "${SPEC_SSH_PUBLIC_KEY}" ]]; then
    SPEC_SSH_PUBLIC_KEY=$(lxc --project "${SPEC_LXD_PROJECT}" config get \
        "${INITIATOR_NODE}" user.user-data 2>/dev/null \
        | awk '/^[[:space:]]*-[[:space:]]+ssh-/ {sub(/^[[:space:]]*-[[:space:]]+/, ""); print; exit}')
fi
if [[ -z "${SPEC_SSH_PUBLIC_KEY}" ]]; then
    log_error "Could not recover the existing cluster SSH public key; refusing to authorize a different key on new nodes"
    exit 1
fi

# -----------------------------------------------------------------------
# Get current node count from Terraform state
# -----------------------------------------------------------------------
CURRENT_NODES=$(tofu output -json node_names 2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
if [[ "${CURRENT_NODES}" -eq 0 ]]; then
    log_error "Could not determine current node count from Terraform state"
    exit 1
fi
if [[ "${CURRENT_NODES}" != "${EXPECTED_CURRENT_NODES}" ]]; then
    log_error "Current node count changed after approval: expected ${EXPECTED_CURRENT_NODES}, found ${CURRENT_NODES}"
    exit 1
fi
if [[ "${CURRENT_NODES}" -ne "${SPEC_NODE_COUNT}" ]]; then
    log_error "Terraform state is inconsistent: node_names=${CURRENT_NODES}, deployment_spec.node_count=${SPEC_NODE_COUNT}"
    exit 1
fi

NEW_TOTAL=$(( CURRENT_NODES + ADD_NODES ))
if [[ "${NEW_TOTAL}" != "${EXPECTED_TARGET_NODES}" ]]; then
    log_error "Target node count differs from the approved plan: expected ${EXPECTED_TARGET_NODES}, calculated ${NEW_TOTAL}"
    exit 1
fi
if (( NEW_TOTAL > 50 )); then
    log_error "Target cluster size ${NEW_TOTAL} exceeds the supported maximum of 50 nodes"
    exit 1
fi

# Even cluster sizes are supported; odd voter counts are often preferred for quorum behavior.
if (( NEW_TOTAL % 2 == 0 )); then
    log_warn "Target cluster size ${NEW_TOTAL} is even — supported, but odd voter counts are often preferred for quorum behavior"
fi

log_info "Current nodes: ${CURRENT_NODES}  →  New total: ${NEW_TOTAL}"

# -----------------------------------------------------------------------
# Enforce immutable existing geometry
# -----------------------------------------------------------------------
assert_matches_spec() {
    local label="$1" requested="$2" actual="$3"
    if [[ -n "${requested}" && "${requested}" != "${actual}" ]]; then
        log_error "${label} override (${requested}) differs from existing deployment (${actual}). Adding nodes cannot resize existing resources."
        exit 1
    fi
}

if [[ -n "${SIZING_TIER}" ]]; then
    log_error "sizing-tier cannot be changed while adding nodes; existing deployment geometry is immutable"
    exit 1
fi

assert_matches_spec "node-cpu" "${NODE_CPU}" "${SPEC_NODE_CPU}"
assert_matches_spec "node-memory-mb" "${NODE_MEMORY_MB}" "${SPEC_NODE_MEMORY_MB}"
assert_matches_spec "root-disk-gib" "${ROOT_DISK_GIB}" "${SPEC_ROOT_DISK_GIB}"
assert_matches_spec "ceph-disk-gib" "${CEPH_DISK_GIB}" "${SPEC_CEPH_DISK_GIB}"
assert_matches_spec "ceph-disks-per-node" "${CEPH_DISKS_PER_NODE}" "${SPEC_CEPH_DISKS_PER_NODE}"
assert_matches_spec "local-disk-gib" "${LOCAL_DISK_GIB}" "${SPEC_LOCAL_DISK_GIB}"

USER_PREFIX="${SPEC_USER_PREFIX}"
NODE_CPU="${SPEC_NODE_CPU}"
NODE_MEMORY_MB="${SPEC_NODE_MEMORY_MB}"
ROOT_DISK_GIB="${SPEC_ROOT_DISK_GIB}"
CEPH_DISK_GIB="${SPEC_CEPH_DISK_GIB}"
CEPH_DISKS_PER_NODE="${SPEC_CEPH_DISKS_PER_NODE}"
LOCAL_DISK_GIB="${SPEC_LOCAL_DISK_GIB}"
export TF_VAR_lxd_project_name="${SPEC_LXD_PROJECT}"
export TF_VAR_lxd_storage_pool="${SPEC_LXD_POOL}"
export TF_VAR_ssh_public_key="${SPEC_SSH_PUBLIC_KEY}"

echo ""
echo "============================================================"
echo "  Adding ${ADD_NODES} node(s) to ${WORKSPACE}"
echo "  New total: ${NEW_TOTAL}"
echo "  vCPU: ${NODE_CPU}  RAM: ${NODE_MEMORY_MB}MiB  Root: ${ROOT_DISK_GIB}GiB  Ceph: ${CEPH_DISK_GIB}GiB × ${CEPH_DISKS_PER_NODE}"
echo "  Network: ${SPEC_NETWORK_MODE}"
echo "  LXD project: ${SPEC_LXD_PROJECT}"
echo "============================================================"
echo ""

# -----------------------------------------------------------------------
# Phase 1: Provision new VMs via Terraform (apply with new count)
# -----------------------------------------------------------------------
log_info "[PHASE 1] Provisioning ${ADD_NODES} new VM(s) with OpenTofu..."

PLAN_FILE=$(mktemp)
trap 'rm -f "${PLAN_FILE}"' EXIT
tofu plan -input=false -parallelism=1 -out="${PLAN_FILE}" \
    -var="user_prefix=${USER_PREFIX}" \
    -var="lxd_project_name=${SPEC_LXD_PROJECT}" \
    -var="management_network_name=${SPEC_MANAGEMENT_NETWORK}" \
    -var="ovn_uplink_network_name=${SPEC_OVN_UPLINK_NETWORK}" \
    -var="ovn_underlay_network_name=${SPEC_OVN_UNDERLAY_NETWORK}" \
    -var="ceph_network_name=${SPEC_CEPH_NETWORK}" \
    -var="ubuntu_image=${SPEC_UBUNTU_IMAGE}" \
    -var="microcloud_node_count=${NEW_TOTAL}" \
    -var="microcloud_node_cpu=${NODE_CPU}" \
    -var="microcloud_node_memory_mb=${NODE_MEMORY_MB}" \
    -var="microcloud_root_disk_size_gib=${ROOT_DISK_GIB}" \
    -var="microcloud_ceph_disk_size_gib=${CEPH_DISK_GIB}" \
    -var="ceph_disks_per_node=${CEPH_DISKS_PER_NODE}" \
    -var="local_disk_size_gib=${LOCAL_DISK_GIB}" \
    -var="microcloud_network_mode=${SPEC_NETWORK_MODE}" \
    -var="microcloud_ovn_underlay_cidr=${SPEC_OVN_UNDERLAY_CIDR}" \
    -var="microcloud_ceph_network_cidr=${SPEC_CEPH_NETWORK_CIDR}"
tofu apply -auto-approve -parallelism=1 "${PLAN_FILE}"

log_success "New VMs provisioned"

# -----------------------------------------------------------------------
# Phase 2: Prepare new nodes only (install snaps, wait for boot)
# -----------------------------------------------------------------------
log_info "[PHASE 2] Preparing new node(s) (snaps, boot wait)..."

INVENTORY_FILE="${REPO_ROOT}/inventory_${WORKSPACE}.yaml"
if [[ ! -f "${INVENTORY_FILE}" ]]; then
    log_error "Ansible inventory not found: ${INVENTORY_FILE}"
    exit 1
fi

# Build limit pattern for only the new nodes
NEW_NODE_LIMIT=""
for i in $(seq $(( CURRENT_NODES + 1 )) "${NEW_TOTAL}"); do
    [[ -n "${NEW_NODE_LIMIT}" ]] && NEW_NODE_LIMIT+=","
    NEW_NODE_LIMIT+="${LXD_PREFIX}-node-${i}"
done

# Run only Play 1 (Prepare MicroCloud Nodes) on the new nodes.
# Play 2 (Bootstrap) is skipped because --limit excludes microcloud[0] unless
# it happens to be a new node (which it won't be for add operations).
ansible-playbook \
    -i "${INVENTORY_FILE}" \
    --limit "${NEW_NODE_LIMIT}" \
    "${PLAYBOOKS_DIR}/microcloud.yml"

log_success "New nodes prepared"

# -----------------------------------------------------------------------
# Phase 3: Expand cluster via 'microcloud preseed' on initiator + joiners
# -----------------------------------------------------------------------
log_info "[PHASE 3] Expanding cluster via 'microcloud preseed'..."

# Derive lookup subnet from the initiator's primary IP
LOOKUP_SUBNET=$(lxc --project "${SPEC_LXD_PROJECT}" exec "${INITIATOR_NODE}" -- bash -c \
    "ip -4 route get 1.1.1.1 | awk '/src/ {for(i=1;i<=NF;i++) if(\$i==\"src\") print \$(i+1)}'" \
    2>/dev/null | head -1 | sed 's/\.[0-9]*$/.0\/24/')

# Build the preseed YAML for add operation.
# Key: initiator is NOT in the systems list → isBootstrap()=false → add mode.
ADD_PRESEED="initiator: ${INITIATOR_NODE}
lookup_subnet: ${LOOKUP_SUBNET}
session_passphrase: microcloud-lab-session-passphrase
systems:"

cidr_host_address() {
    local cidr="$1"
    local offset="$2"
    python3 - "${cidr}" "${offset}" <<'PY'
import ipaddress
import sys

print(ipaddress.ip_network(sys.argv[1], strict=True)[int(sys.argv[2])])
PY
}

for i in $(seq $(( CURRENT_NODES + 1 )) "${NEW_TOTAL}"); do
    node_name="${LXD_PREFIX}-node-${i}"

    if [[ "${SPEC_NETWORK_MODE}" == "fully-segregated-4nic" ]]; then
        OVN_IFACE="ovn-uplink"
        OVN_UNDERLAY_IP=$(cidr_host_address "${SPEC_OVN_UNDERLAY_CIDR}" $((i + 9)))
    else
        OVN_IFACE=$(lxc --project "${SPEC_LXD_PROJECT}" exec "${node_name}" -- bash -c "
            primary=\$(ip -4 route show default 2>/dev/null | awk '/default/ {print \$5; exit}')
            for iface in \$(ip -o link show | awk -F': ' '!/lo/ {print \$2}' | cut -d'@' -f1); do
                [ \"\$iface\" = \"\$primary\" ] && continue
                ip -4 addr show \"\$iface\" | grep -q 'inet ' || { echo \"\$iface\"; break; }
            done
        " 2>/dev/null | tr -d '[:space:]')
        OVN_UNDERLAY_IP=""
    fi

    if [[ "${LOCAL_DISK_GIB}" -gt 0 ]]; then
        LOCAL_DISK=$(lxc --project "${SPEC_LXD_PROJECT}" exec "${node_name}" -- bash -c "
            serial='lxd_local--disk'
            lsblk -dn -o NAME,SERIAL | awk -v serial=\"\$serial\" '\$2 == serial {print \"/dev/\" \$1; exit}'
        " 2>/dev/null | tr -d '[:space:]')
        if [[ -z "${LOCAL_DISK}" ]]; then
            log_error "${node_name}: expected local disk serial lxd_local--disk was not found"
            exit 1
        fi
    else
        LOCAL_DISK=""
    fi

    readarray -t CEPH_DISK_LIST < <(lxc --project "${SPEC_LXD_PROJECT}" exec "${node_name}" -- bash -c "
        expected=${CEPH_DISKS_PER_NODE}
        for index in \$(seq 1 \"\$expected\"); do
            serial=\"lxd_ceph--disk--\$index\"
            path=\$(lsblk -dn -o NAME,SERIAL | awk -v serial=\"\$serial\" '\$2 == serial {print \"/dev/\" \$1; exit}')
            [ -n \"\$path\" ] && echo \"\$path\"
        done
    " 2>/dev/null)

    if [[ "${#CEPH_DISK_LIST[@]}" -ne "${CEPH_DISKS_PER_NODE}" ]]; then
        log_error "${node_name}: expected ${CEPH_DISKS_PER_NODE} Ceph disk(s), detected ${#CEPH_DISK_LIST[@]}"
        exit 1
    fi

    ADD_PRESEED+="
  - name: ${node_name}
    ovn_uplink_interface: ${OVN_IFACE}"
    if [[ -n "${OVN_UNDERLAY_IP}" ]]; then
        ADD_PRESEED+="
    ovn_underlay_ip: ${OVN_UNDERLAY_IP}"
    fi
    if [[ ${#CEPH_DISK_LIST[@]} -gt 0 ]]; then
        ADD_PRESEED+="
    storage:
      ceph:"
        for disk in "${CEPH_DISK_LIST[@]}"; do
            ADD_PRESEED+="
        - path: ${disk}
          wipe: true"
        done
        if [[ -n "${LOCAL_DISK}" ]]; then
            ADD_PRESEED+="
      local:
        path: ${LOCAL_DISK}
        wipe: true"
        fi
    fi

    log_info "  ${node_name}: OVN=${OVN_IFACE:-?}  Underlay=${OVN_UNDERLAY_IP:-shared}  Ceph=${CEPH_DISK_LIST[*]:-none}  Local=${LOCAL_DISK:-none}"
done

# Start preseed on joiner nodes first (they enter joining session)
JOINER_PIDS=()
for i in $(seq $(( CURRENT_NODES + 1 )) "${NEW_TOTAL}"); do
    node_name="${LXD_PREFIX}-node-${i}"
    echo "${ADD_PRESEED}" | lxc --project "${SPEC_LXD_PROJECT}" exec \
        "${node_name}" -- microcloud preseed &
    JOINER_PIDS+=($!)
done

# Brief pause to let joiners start their session
sleep 3

# Run preseed on the initiator (enters initiating/add session)
log_info "Running microcloud preseed on ${INITIATOR_NODE} (add mode)..."
echo "${ADD_PRESEED}" | lxc --project "${SPEC_LXD_PROJECT}" exec \
    "${INITIATOR_NODE}" -- microcloud preseed

# Wait for all joiner processes to complete
JOINER_FAILED=false
for pid in "${JOINER_PIDS[@]}"; do
    if ! wait "${pid}"; then
        JOINER_FAILED=true
    fi
done

if [[ "${JOINER_FAILED}" == "true" ]]; then
    log_error "One or more joiner preseed processes failed"
    exit 1
fi

log_success "Cluster expanded to ${NEW_TOTAL} nodes"

# -----------------------------------------------------------------------
# Phase 4: Verify expanded cluster
# -----------------------------------------------------------------------
log_info "[PHASE 4] Verifying expanded cluster..."
"${SCRIPT_DIR}/verify_cluster_health.sh" --workspace="${WORKSPACE}"

log_success "Done — workspace: ${WORKSPACE}  nodes: ${NEW_TOTAL}"
