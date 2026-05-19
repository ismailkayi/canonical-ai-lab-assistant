#!/usr/bin/env bash
# add_cluster_node.sh — Add one or more nodes to an existing MicroCloud cluster
#
# Flow:
#   1. Determine current node count from Terraform state
#   2. Provision new VMs via tofu apply (increment node count)
#   3. Prepare new nodes (install snaps, detect resources)
#   4. Run `microcloud add` on the existing initiator to expand the cluster
#
# Usage:
#   add_cluster_node.sh --workspace=<name> --add-nodes=<N>
#                       [--ceph-disk-gib=<gib>] [--ceph-disks-per-node=<n>]
#                       [--local-disk-gib=<gib>] [--sizing-tier=<tier>]
#                       [--node-cpu=<n>] [--node-memory-mb=<mb>]
#                       [--root-disk-gib=<gib>]

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
CEPH_DISKS_PER_NODE=1
LOCAL_DISK_GIB=0
SSH_KEY_PATH="$HOME/.ssh/id_rsa_lab"

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
        *) log_error "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

if [[ -z "${WORKSPACE}" ]]; then
    log_error "Usage: $0 --workspace=<name> --add-nodes=<N>"
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
for tool in tofu ansible lxc; do
    command -v "${tool}" &>/dev/null || { log_error "${tool} not found"; exit 1; }
done

# -----------------------------------------------------------------------
# SSH key
# -----------------------------------------------------------------------
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
    log_info "Generating SSH key at ${SSH_KEY_PATH} ..."
    ssh-keygen -t rsa -b 4096 -f "${SSH_KEY_PATH}" -N "" -q
fi
export TF_VAR_ssh_public_key
TF_VAR_ssh_public_key="$(cat "${SSH_KEY_PATH}.pub")"

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

# -----------------------------------------------------------------------
# Get current node count from Terraform state
# -----------------------------------------------------------------------
CURRENT_NODES=$(tofu output -json node_names 2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
if [[ "${CURRENT_NODES}" -eq 0 ]]; then
    log_error "Could not determine current node count from Terraform state"
    exit 1
fi

NEW_TOTAL=$(( CURRENT_NODES + ADD_NODES ))

# MicroCloud requires odd cluster size for quorum — warn but don't block (user may know what they're doing)
if (( NEW_TOTAL % 2 == 0 )); then
    log_warn "Target cluster size ${NEW_TOTAL} is even — recommend odd count for MicroCloud quorum"
fi

log_info "Current nodes: ${CURRENT_NODES}  →  New total: ${NEW_TOTAL}"

# -----------------------------------------------------------------------
# Detect LXD defaults (network + storage pool)
# -----------------------------------------------------------------------
get_lxd_network() {
    local name ipv4
    while IFS= read -r name; do
        [[ -z "${name}" ]] && continue
        local type; type=$(lxc network show "${name}" 2>/dev/null | awk -F': ' '$1=="type" {print $2; exit}')
        [[ "${type}" != "bridge" ]] && continue
        ipv4=$(lxc network get "${name}" ipv4.address 2>/dev/null || true)
        if [[ -n "${ipv4}" && "${ipv4}" != "none" ]]; then echo "${name}"; return; fi
    done < <(lxc network list --format csv | awk -F',' 'NF>0 {print $1}')
    echo "lxdbr0"
}

get_lxd_pool() {
    lxc storage show default &>/dev/null && echo "default" && return
    lxc storage list --format csv | awk -F',' 'NR==1 {print $1}'
}

export TF_VAR_lxd_network_name; TF_VAR_lxd_network_name="$(get_lxd_network)"
export TF_VAR_lxd_storage_pool; TF_VAR_lxd_storage_pool="$(get_lxd_pool)"

# -----------------------------------------------------------------------
# Auto-size new nodes if not specified
# -----------------------------------------------------------------------
if [[ -z "${NODE_CPU}" || -z "${NODE_MEMORY_MB}" || -z "${ROOT_DISK_GIB}" || -z "${CEPH_DISK_GIB}" ]]; then
    # Re-use sizing from existing nodes if possible
    log_info "Inheriting sizing from existing cluster node..."
    EXISTING_CPU=$(lxc config show "${INITIATOR_NODE}" 2>/dev/null | awk '/limits.cpu:/ {print $2}' || echo "2")
    EXISTING_MEM=$(lxc config show "${INITIATOR_NODE}" 2>/dev/null | awk '/limits.memory:/ {gsub(/MiB/,"",$2); print $2}' || echo "4096")
    NODE_CPU="${NODE_CPU:-${EXISTING_CPU:-2}}"
    NODE_MEMORY_MB="${NODE_MEMORY_MB:-${EXISTING_MEM:-4096}}"
    ROOT_DISK_GIB="${ROOT_DISK_GIB:-40}"
    CEPH_DISK_GIB="${CEPH_DISK_GIB:-50}"
fi

# Derive user_prefix from workspace name (strip _microcloud suffix)
USER_PREFIX="${WORKSPACE%_microcloud}"

echo ""
echo "============================================================"
echo "  Adding ${ADD_NODES} node(s) to ${WORKSPACE}"
echo "  New total: ${NEW_TOTAL}"
echo "  vCPU: ${NODE_CPU}  RAM: ${NODE_MEMORY_MB}MiB  Root: ${ROOT_DISK_GIB}GiB  Ceph: ${CEPH_DISK_GIB}GiB × ${CEPH_DISKS_PER_NODE}"
echo "============================================================"
echo ""

# -----------------------------------------------------------------------
# Phase 1: Provision new VMs via Terraform (apply with new count)
# -----------------------------------------------------------------------
log_info "[PHASE 1] Provisioning ${ADD_NODES} new VM(s) with OpenTofu..."

tofu apply -auto-approve \
    -var="user_prefix=${USER_PREFIX}" \
    -var="microcloud_node_count=${NEW_TOTAL}" \
    -var="microcloud_node_cpu=${NODE_CPU}" \
    -var="microcloud_node_memory_mb=${NODE_MEMORY_MB}" \
    -var="microcloud_root_disk_size_gib=${ROOT_DISK_GIB}" \
    -var="microcloud_ceph_disk_size_gib=${CEPH_DISK_GIB}" \
    -var="ceph_disks_per_node=${CEPH_DISKS_PER_NODE}" \
    -var="local_disk_size_gib=${LOCAL_DISK_GIB}"

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

# Build a temporary inventory for only the new nodes
NEW_NODES_INI="${REPO_ROOT}/.tmp_new_nodes_${WORKSPACE}.ini"
{
    echo "[new_microcloud_nodes]"
    for i in $(seq $(( CURRENT_NODES + 1 )) "${NEW_TOTAL}"); do
        echo "${LXD_PREFIX}-node-${i} ansible_connection=lxd"
    done
} > "${NEW_NODES_INI}"
trap 'rm -f "${NEW_NODES_INI}"' EXIT

ansible-playbook \
    -i "${NEW_NODES_INI}" \
    "${PLAYBOOKS_DIR}/prepare_nodes.yml" \
    2>/dev/null \
    || ansible-playbook \
        -i "${NEW_NODES_INI}" \
        --tags "wait_boot,install_snaps" \
        "${PLAYBOOKS_DIR}/microcloud.yml"

log_success "New nodes prepared"

# -----------------------------------------------------------------------
# Phase 3: Run `microcloud add` on the initiator to expand the cluster
# -----------------------------------------------------------------------
log_info "[PHASE 3] Expanding cluster via 'microcloud add'..."

# Build the preseed for add operation
# microcloud add uses a simpler preseed: just list new joiner nodes
NEW_NODES_LIST=""
for i in $(seq $(( CURRENT_NODES + 1 )) "${NEW_TOTAL}"); do
    node_name="${LXD_PREFIX}-node-${i}"
    NEW_NODES_LIST+="${node_name} "

    # Detect OVN and Ceph disks on the new node
    OVN_IFACE=$(lxc exec "${node_name}" -- bash -c "
        primary=\$(ip -4 route show default 2>/dev/null | awk '/default/ {print \$5; exit}')
        for iface in \$(ip -o link show | awk -F': ' '!/lo/ {print \$2}' | cut -d'@' -f1); do
            [ \"\$iface\" = \"\$primary\" ] && continue
            ip -4 addr show \"\$iface\" | grep -q 'inet ' || { echo \"\$iface\"; break; }
        done
    " 2>/dev/null | tr -d '[:space:]')

    CEPH_DISKS=$(lxc exec "${node_name}" -- bash -c "
        root_src=\$(findmnt -n -o SOURCE /)
        root_disk=\$(lsblk -no PKNAME \"\$root_src\" 2>/dev/null || true)
        for disk in \$(lsblk -dn -o NAME,TYPE | awk '\$2==\"disk\" {print \$1}'); do
            [ \"\$disk\" = \"\$root_disk\" ] && continue
            lsblk -nr -o MOUNTPOINT \"/dev/\$disk\" | grep -q '[^[:space:]]' && continue
            echo \"/dev/\$disk\"
        done
    " 2>/dev/null)

    log_info "  ${node_name}: OVN=${OVN_IFACE:-?}  Ceph disks=$(echo "${CEPH_DISKS}" | tr '\n' ' ')"
done

# Write add preseed and pipe it into microcloud add on the initiator
ADD_PRESEED=$(cat <<EOF
lookup_subnet: $(lxc exec "${INITIATOR_NODE}" -- bash -c "ip -4 route get 1.1.1.1 | awk '/src/ {for(i=1;i<=NF;i++) if(\$i==\"src\") print \$(i+1)}'" 2>/dev/null | head -1 | sed 's/\.[0-9]*$/.0\/24/')
session_passphrase: microcloud-lab-session-passphrase
systems:
EOF
)

for i in $(seq $(( CURRENT_NODES + 1 )) "${NEW_TOTAL}"); do
    node_name="${LXD_PREFIX}-node-${i}"

    OVN_IFACE=$(lxc exec "${node_name}" -- bash -c "
        primary=\$(ip -4 route show default 2>/dev/null | awk '/default/ {print \$5; exit}')
        for iface in \$(ip -o link show | awk -F': ' '!/lo/ {print \$2}' | cut -d'@' -f1); do
            [ \"\$iface\" = \"\$primary\" ] && continue
            ip -4 addr show \"\$iface\" | grep -q 'inet ' || { echo \"\$iface\"; break; }
        done
    " 2>/dev/null | tr -d '[:space:]')

    readarray -t CEPH_DISK_LIST < <(lxc exec "${node_name}" -- bash -c "
        root_src=\$(findmnt -n -o SOURCE /)
        root_disk=\$(lsblk -no PKNAME \"\$root_src\" 2>/dev/null || true)
        for disk in \$(lsblk -dn -o NAME,TYPE | awk '\$2==\"disk\" {print \$1}'); do
            [ \"\$disk\" = \"\$root_disk\" ] && continue
            lsblk -nr -o MOUNTPOINT \"/dev/\$disk\" | grep -q '[^[:space:]]' && continue
            echo \"/dev/\$disk\"
        done
    " 2>/dev/null)

    ADD_PRESEED+="
  - name: ${node_name}
    ovn_uplink_interface: ${OVN_IFACE}"
    if [[ ${#CEPH_DISK_LIST[@]} -gt 0 ]]; then
        ADD_PRESEED+="
    storage:
      ceph:"
        for disk in "${CEPH_DISK_LIST[@]}"; do
            ADD_PRESEED+="
        - path: ${disk}
          wipe: true"
        done
    fi
done

log_info "Running microcloud add on ${INITIATOR_NODE}..."
echo "${ADD_PRESEED}" | lxc exec "${INITIATOR_NODE}" -- microcloud add < /dev/stdin || \
    echo "${ADD_PRESEED}" | lxc exec "${INITIATOR_NODE}" -- bash -c "microcloud add < /dev/stdin"

log_success "Cluster expanded to ${NEW_TOTAL} nodes"

# -----------------------------------------------------------------------
# Phase 4: Verify expanded cluster
# -----------------------------------------------------------------------
log_info "[PHASE 4] Verifying expanded cluster..."
"${SCRIPT_DIR}/verify_cluster_health.sh" --workspace="${WORKSPACE}"

log_success "Done — workspace: ${WORKSPACE}  nodes: ${NEW_TOTAL}"
