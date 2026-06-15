#!/usr/bin/env bash
# deploy_microcloud.sh — MicroCloud deployment via OpenTofu + Ansible
# All parameters are passed as --key=value by the Python orchestrator.
set -euo pipefail

# -----------------------------------------------------------------------
# Colors & helpers
# -----------------------------------------------------------------------
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

print_divider() { printf '%s\n' "------------------------------------------------------------"; }
print_section() { echo ""; print_divider; echo "$1"; print_divider; }
print_kv()      { printf '  %-30s %s\n' "$1" "$2"; }

# -----------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------
SCENARIO="standard"
NODES=3
SIZING_TIER=""
NODE_CPU=""
NODE_MEMORY_MB=""
ROOT_DISK_GIB=""
CEPH_DISK_GIB=""
CEPH_DISKS_PER_NODE=1
LOCAL_DISK_GIB=0
USER_PREFIX="lab"
AUTO_APPROVE=false
SSH_KEY_PATH="$HOME/.ssh/id_rsa_lab"
NETWORK_INTERFACE=""
OVN_UPLINK_INTERFACE=""
CEPH_OSD_DISK=""

# -----------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------
for arg in "$@"; do
    case "${arg}" in
        --scenario=*)       SCENARIO="${arg#*=}" ;;
        --nodes=*)          NODES="${arg#*=}" ;;
        --sizing-tier=*)    SIZING_TIER="${arg#*=}" ;;
        --node-cpu=*)       NODE_CPU="${arg#*=}" ;;
        --node-memory-mb=*) NODE_MEMORY_MB="${arg#*=}" ;;
        --root-disk-gib=*)  ROOT_DISK_GIB="${arg#*=}" ;;
        --ceph-disk-gib=*)  CEPH_DISK_GIB="${arg#*=}" ;;
        --ceph-disks-per-node=*) CEPH_DISKS_PER_NODE="${arg#*=}" ;;
        --local-disk-gib=*)  LOCAL_DISK_GIB="${arg#*=}" ;;
        --network-interface=*) NETWORK_INTERFACE="${arg#*=}" ;;
        --ovn-uplink-interface=*) OVN_UPLINK_INTERFACE="${arg#*=}" ;;
        --ceph-osd-disk=*) CEPH_OSD_DISK="${arg#*=}" ;;
        --user-prefix=*)    USER_PREFIX="${arg#*=}" ;;
        --ssh-key=*)        SSH_KEY_PATH="${arg#*=}" ;;
        --auto-approve)     AUTO_APPROVE=true ;;
        *) log_error "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

# -----------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/terraform"
PLAYBOOKS_DIR="${REPO_ROOT}/playbooks"

[[ ! -d "${TERRAFORM_DIR}" ]] && { log_error "terraform/ not found"; exit 1; }

# -----------------------------------------------------------------------
# Tool checks
# -----------------------------------------------------------------------
for tool in tofu ansible; do
    if ! command -v "${tool}" &>/dev/null; then
        log_error "${tool} not found. Run: lab-ai bootstrap"
        exit 1
    fi
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
# Detect LXD network & storage pool
# -----------------------------------------------------------------------
detect_lxd_defaults() {
    local detected_network="" detected_pool="" candidate_name="" net_type="" ipv4_addr=""

    if ! command -v lxc &>/dev/null || ! lxc info >/dev/null 2>&1; then
        log_error "LXD not reachable. Run: lab-ai bootstrap"
        exit 1
    fi

    while IFS= read -r candidate_name; do
        [[ -z "${candidate_name}" ]] && continue
        net_type=$(lxc network show "${candidate_name}" 2>/dev/null | awk -F': ' '$1=="type" {print $2; exit}')
        [[ "${net_type}" != "bridge" ]] && continue
        ipv4_addr=$(lxc network get "${candidate_name}" ipv4.address 2>/dev/null || true)
        if [[ "${candidate_name}" == "lxdbr0" && -n "${ipv4_addr}" && "${ipv4_addr}" != "none" ]]; then
            detected_network="${candidate_name}"; break
        fi
        if [[ -z "${detected_network}" && -n "${ipv4_addr}" && "${ipv4_addr}" != "none" ]]; then
            detected_network="${candidate_name}"
        fi
    done < <(lxc network list --format csv | awk -F',' 'NF>0 {print $1}')

    if [[ -z "${detected_network}" ]]; then
        lxc network show labbr0 >/dev/null 2>&1 \
            || lxc network create labbr0 ipv4.address=auto ipv6.address=none >/dev/null 2>&1 \
            || true
        detected_network="labbr0"
    fi

    if lxc storage show default >/dev/null 2>&1; then
        detected_pool="default"
    else
        detected_pool=$(lxc storage list --format csv | awk -F',' 'NR==1 {print $1}')
    fi

    [[ -z "${detected_pool}" ]] && { log_error "No LXD storage pool found"; exit 1; }

    export TF_VAR_lxd_network_name="${detected_network}"
    export TF_VAR_lxd_storage_pool="${detected_pool}"

    print_kv "LXD network" "${detected_network}"
    print_kv "LXD storage pool" "${detected_pool}"
}

# -----------------------------------------------------------------------
# Auto-sizing (ported from orchestrate.sh configure_microcloud_sizing)
# -----------------------------------------------------------------------
pick_floor_tier() {
    local limit="$1"; shift; local selected="$1"
    for tier in "$@"; do
        if (( tier <= limit )); then
            selected="${tier}"
        else
            break
        fi
    done
    echo "${selected}"
}
pick_previous_tier() {
    local current="$1"; shift; local previous="$1"
    for tier in "$@"; do
        if (( tier >= current )); then
            break
        fi
        previous="${tier}"
    done
    echo "${previous}"
}
pick_next_tier() {
    local current="$1" limit="$2"; shift 2
    for tier in "$@"; do
        if (( tier > current && tier <= limit )); then
            echo "${tier}"
            return
        fi
    done
    echo "${current}"
}
round_down_even() {
    local v="$1" min="${2:-2}"
    if (( v < min )); then
        echo "${min}"
        return
    fi
    if (( v % 2 != 0 )); then
        v=$(( v - 1 ))
    fi
    echo "${v}"
}

get_storage_available_gib() {
    local pool="${TF_VAR_lxd_storage_pool:-default}"
    local info line gib num unit
    info=$(lxc storage info "${pool}" 2>/dev/null || true)
    line=$(echo "${info}" | awk -F': ' '/Space available:/ {print $2; exit}')
    if [[ -n "${line}" ]]; then
        num=$(echo "${line}" | grep -Eo '[0-9]+([.][0-9]+)?' | head -1)
        unit=$(echo "${line}" | grep -Eo '[A-Za-z]+' | tail -1)
        gib=$(awk -v n="${num}" -v u="${unit}" 'BEGIN {
            if (u=="TiB") print int(n*1024)
            else if (u=="GiB") print int(n)
            else if (u=="MiB") print int(n/1024)
            else print ""
        }')
        [[ -n "${gib}" ]] && echo "${gib}" && return
    fi
    df -BG . 2>/dev/null | awk 'NR==2 {gsub(/G/,"",$4); print $4}' || echo "200"
}

auto_size_nodes() {
    local cpu_total ram_mb storage_gib host_ram_gb
    cpu_total=$(nproc 2>/dev/null || echo 4)
    ram_mb=$(awk '/MemTotal:/ {print int($2/1024)}' /proc/meminfo)
    storage_gib=$(get_storage_available_gib)
    host_ram_gb=$(( (ram_mb + 1023) / 1024 ))

    local reserve_cpu=$(( cpu_total / 5 ))
    if (( reserve_cpu < 2 )); then reserve_cpu=2; fi
    local usable_cpu=$(( cpu_total - reserve_cpu ))
    if (( usable_cpu < NODES )); then usable_cpu=${NODES}; fi

    local reserve_mb=$(( ram_mb / 5 ))
    if (( reserve_mb < 4096 )); then reserve_mb=4096; fi
    local usable_mb=$(( ram_mb - reserve_mb ))
    if (( usable_mb < NODES * 4096 )); then usable_mb=$(( NODES * 4096 )); fi
    local usable_ram_gb=$(( usable_mb / 1024 ))

    local usable_disk=$(( storage_gib - 20 ))
    if (( usable_disk < 120 )); then usable_disk=120; fi

    local bal_cpu; bal_cpu=$(round_down_even $(( usable_cpu / NODES )) 2)
    local bal_ram; bal_ram=$(pick_floor_tier $(( usable_ram_gb / NODES )) 8 12 16 24 32 48 64 96 128)
    local raw_ceph=$(( (usable_disk / NODES) - 40 ))
    if (( raw_ceph < 20 )); then raw_ceph=20; fi
    local bal_ceph; bal_ceph=$(pick_floor_tier "${raw_ceph}" 20 50 100 150 200 250 300 400 500)

    case "${SIZING_TIER:-balanced}" in
        minimal|conservative)
            NODE_CPU=$(round_down_even $(( bal_cpu - 2 )) 2)
            NODE_MEMORY_MB=$(( $(pick_previous_tier "${bal_ram}" 4 8 12 16 24 32 48 64 96 128) * 1024 ))
            ROOT_DISK_GIB=30
            CEPH_DISK_GIB=$(pick_previous_tier "${bal_ceph}" 20 50 100 150 200 250 300 400 500)
            ;;
        performance)
            NODE_CPU=$(( bal_cpu + 2 ))
            NODE_MEMORY_MB=$(( $(pick_next_tier "${bal_ram}" "$(pick_floor_tier $(( host_ram_gb / NODES )) 8 12 16 24 32 48 64 96 128)" 8 12 16 24 32 48 64 96 128) * 1024 ))
            ROOT_DISK_GIB=50
            CEPH_DISK_GIB=$(pick_next_tier "${bal_ceph}" "$(pick_floor_tier $(( storage_gib / NODES - 50 )) 20 50 100 150 200 250 300 400 500)" 20 50 100 150 200 250 300 400 500)
            ;;
        *)  # balanced / small / medium / large
            NODE_CPU="${bal_cpu}"
            NODE_MEMORY_MB=$(( bal_ram * 1024 ))
            ROOT_DISK_GIB=40
            CEPH_DISK_GIB="${bal_ceph}"
            ;;
    esac

    if (( NODE_CPU < 1 )); then NODE_CPU=1; fi
    if (( NODE_MEMORY_MB < 1024 )); then NODE_MEMORY_MB=1024; fi
    if (( ROOT_DISK_GIB < 20 )); then ROOT_DISK_GIB=20; fi
    if (( CEPH_DISK_GIB < 10 )); then CEPH_DISK_GIB=10; fi
}

# -----------------------------------------------------------------------
# Post-deploy summary
# -----------------------------------------------------------------------
print_microcloud_summary() {
    local env_name="$1"
    local lxd_prefix="${env_name//_/-}"

    print_section "MicroCloud Deployment Summary"
    print_kv "Environment" "${env_name}"
    print_kv "Scenario" "${SCENARIO}"
    print_kv "Nodes" "${NODES}"
    echo ""
    printf '  %-28s %-16s %s\n' "Node" "IP" "LXD UI"
    printf '  %-28s %-16s %s\n' "----------------------------" "----------------" "------------------------------"

    for i in $(seq 1 "${NODES}"); do
        local node="${lxd_prefix}-node-${i}"
        local ip
        ip=$(lxc exec "${node}" -- sh -c \
            "ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if(\$i==\"src\"){print \$(i+1);exit}}'" \
            2>/dev/null || echo "N/A")
        ip="$(echo "${ip}" | tr -d '[:space:]')"
        [[ -z "${ip}" ]] && ip="N/A"
        local ui="-"; [[ "${ip}" != "N/A" ]] && ui="https://${ip}:8443"
        printf '  %-28s %-16s %s\n' "${node}" "${ip}" "${ui}"
    done
}

# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------
WORKSPACE_NAME="${USER_PREFIX}_microcloud"

print_section "MicroCloud Deployment — scenario: ${SCENARIO}"

detect_lxd_defaults

if [[ -z "${NODE_CPU}" || -z "${NODE_MEMORY_MB}" ]]; then
    log_info "Auto-sizing nodes (tier: ${SIZING_TIER:-balanced}) ..."
    auto_size_nodes
fi

NODE_MEMORY_GB=$(( (NODE_MEMORY_MB + 1023) / 1024 ))

print_kv "Scenario" "${SCENARIO}"
print_kv "Nodes" "${NODES}"
print_kv "vCPU / node" "${NODE_CPU}"
print_kv "RAM / node" "${NODE_MEMORY_GB} GB"
print_kv "Root disk / node" "${ROOT_DISK_GIB} GiB"
print_kv "Ceph disk / node" "${CEPH_DISK_GIB} GiB"
print_kv "Ceph OSDs / node" "${CEPH_DISKS_PER_NODE}"
print_kv "Local disk / node" "$([ "${LOCAL_DISK_GIB}" -gt 0 ] && echo "${LOCAL_DISK_GIB} GiB (ZFS)" || echo "disabled")"
print_kv "Cluster NIC" "${NETWORK_INTERFACE:-auto-detect}"
print_kv "OVN uplink NIC" "${OVN_UPLINK_INTERFACE:-auto-detect}"
print_kv "Ceph OSD disk" "${CEPH_OSD_DISK:-auto-detect}"
print_kv "Workspace" "${WORKSPACE_NAME}"
echo ""

if [[ "${AUTO_APPROVE}" != "true" ]]; then
    read -r -p "Proceed with deployment? [y/N] " confirm
    [[ "${confirm,,}" != "y" ]] && { log_warn "Aborted."; exit 0; }
fi

# --- OpenTofu provisioning ---
cd "${TERRAFORM_DIR}"
tofu init -input=false >/dev/null 2>&1

tofu workspace list | tr -d '* ' | grep -qx "${WORKSPACE_NAME}" \
    || tofu workspace new "${WORKSPACE_NAME}" >/dev/null 2>&1
tofu workspace select "${WORKSPACE_NAME}" >/dev/null 2>&1

log_info "Running tofu apply ..."
tofu apply -auto-approve -parallelism=1 \
    -var="user_prefix=${USER_PREFIX}" \
    -var="microcloud_node_count=${NODES}" \
    -var="microcloud_node_cpu=${NODE_CPU}" \
    -var="microcloud_node_memory_mb=${NODE_MEMORY_MB}" \
    -var="microcloud_root_disk_size_gib=${ROOT_DISK_GIB}" \
    -var="microcloud_ceph_disk_size_gib=${CEPH_DISK_GIB}" \
    -var="ceph_disks_per_node=${CEPH_DISKS_PER_NODE}" \
    -var="local_disk_size_gib=${LOCAL_DISK_GIB}"

log_success "LXD VMs provisioned"

# --- Ansible bootstrap ---
INVENTORY_FILE="${REPO_ROOT}/inventory_${WORKSPACE_NAME}.yaml"

if [[ ! -f "${INVENTORY_FILE}" ]]; then
    log_error "Ansible inventory not found: ${INVENTORY_FILE}"
    exit 1
fi

log_info "Running Ansible playbook (microcloud.yml) ..."
ansible-playbook \
    -i "${INVENTORY_FILE}" \
    "${PLAYBOOKS_DIR}/microcloud.yml"

log_success "MicroCloud cluster bootstrapped"

print_microcloud_summary "${WORKSPACE_NAME}"
log_success "Done — workspace: ${WORKSPACE_NAME}"
