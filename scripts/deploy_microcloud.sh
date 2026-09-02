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
NETWORK_MODE="standard-2nic"
OVN_UNDERLAY_CIDR=""
CEPH_NETWORK_CIDR=""
RESOURCE_NAMESPACE=""
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
        --network-mode=*)    NETWORK_MODE="${arg#*=}" ;;
        --ovn-underlay-cidr=*) OVN_UNDERLAY_CIDR="${arg#*=}" ;;
        --ceph-network-cidr=*) CEPH_NETWORK_CIDR="${arg#*=}" ;;
        --resource-namespace=*) RESOURCE_NAMESPACE="${arg#*=}" ;;
        --network-interface=*) NETWORK_INTERFACE="${arg#*=}" ;;
        --ovn-uplink-interface=*) OVN_UPLINK_INTERFACE="${arg#*=}" ;;
        --ceph-osd-disk=*) CEPH_OSD_DISK="${arg#*=}" ;;
        --user-prefix=*)    USER_PREFIX="${arg#*=}" ;;
        --ssh-key=*)        SSH_KEY_PATH="${arg#*=}" ;;
        --auto-approve)     AUTO_APPROVE=true ;;
        *) log_error "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

if [[ "${NETWORK_MODE}" != "standard-2nic" \
        && "${NETWORK_MODE}" != "fully-segregated-4nic" ]]; then
    log_error "network-mode must be standard-2nic or fully-segregated-4nic"
    exit 1
fi
if [[ "${NETWORK_MODE}" == "fully-segregated-4nic" \
        && ( -z "${OVN_UNDERLAY_CIDR}" || -z "${CEPH_NETWORK_CIDR}" ) ]]; then
    log_error "fully-segregated-4nic requires --ovn-underlay-cidr and --ceph-network-cidr"
    exit 1
fi
if [[ "${NETWORK_MODE}" == "standard-2nic" \
        && ( -n "${OVN_UNDERLAY_CIDR}" || -n "${CEPH_NETWORK_CIDR}" ) ]]; then
    log_error "Dedicated plane CIDRs require fully-segregated-4nic mode"
    exit 1
fi
if [[ "${NETWORK_MODE}" == "fully-segregated-4nic" ]]; then
    if ! python3 - "${OVN_UNDERLAY_CIDR}" "${CEPH_NETWORK_CIDR}" "${NODES}" <<'PY'
import ipaddress
import sys

ovn = ipaddress.ip_network(sys.argv[1], strict=True)
ceph = ipaddress.ip_network(sys.argv[2], strict=True)
nodes = int(sys.argv[3])
if ovn.version != 4 or ceph.version != 4:
    raise SystemExit("dedicated plane CIDRs must be IPv4")
if ovn.overlaps(ceph):
    raise SystemExit("OVN underlay and Ceph network CIDRs must not overlap")
if nodes + 9 >= ovn.num_addresses - 1 or nodes + 9 >= ceph.num_addresses - 1:
    raise SystemExit("dedicated plane CIDRs do not have enough node addresses")
for tenant in (ipaddress.ip_network("192.168.250.0/24"), ipaddress.ip_network("10.250.1.0/24")):
    if ovn.overlaps(tenant) or ceph.overlaps(tenant):
        raise SystemExit(f"dedicated plane CIDRs must not overlap reserved OVN subnet {tenant}")
PY
    then
        log_error "Invalid fully segregated network geometry"
        exit 1
    fi
fi

# -----------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TERRAFORM_DIR="${LAB_AI_TERRAFORM_DIR:-${REPO_ROOT}/terraform}"
PLAYBOOKS_DIR="${REPO_ROOT}/playbooks"
RUNTIME_DIR="$(dirname "${TERRAFORM_DIR}")"

[[ ! -d "${TERRAFORM_DIR}" ]] && { log_error "terraform/ not found"; exit 1; }

# -----------------------------------------------------------------------
# Tool checks
# -----------------------------------------------------------------------
for tool in tofu ansible flock; do
    if ! command -v "${tool}" &>/dev/null; then
        log_error "${tool} not found. Run: lab-ai bootstrap"
        exit 1
    fi
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

validate_plane_subnet_availability() {
    if [[ "${NETWORK_MODE}" != "fully-segregated-4nic" ]]; then
        return 0
    fi

    local existing_subnets=""
    existing_subnets=$(
        {
            ip -o -4 route show table all 2>/dev/null \
                | awk '$1 != "default" {for (i=1; i<=NF; i++) if ($i ~ /^[0-9]+\./ && $i ~ /\//) {print $i; break}}'
            while IFS= read -r network; do
                [[ -z "${network}" ]] && continue
                lxc network get "${network}" ipv4.address 2>/dev/null || true
                lxc network get "${network}" user.canonical-ai-lab-assistant.cidr \
                    2>/dev/null || true
            done < <(lxc network list --format csv 2>/dev/null | awk -F',' 'NF {print $1}')
        } | sort -u
    )

    if ! python3 - "${OVN_UNDERLAY_CIDR}" "${CEPH_NETWORK_CIDR}" \
            "${existing_subnets}" <<'PY'
import ipaddress
import sys

ovn = ipaddress.ip_network(sys.argv[1], strict=True)
ceph = ipaddress.ip_network(sys.argv[2], strict=True)
for line in sys.argv[3].splitlines():
    try:
        existing = ipaddress.ip_network(line.strip(), strict=False)
    except ValueError:
        continue
    if existing.prefixlen == 0:
        continue
    for label, candidate in (("OVN underlay", ovn), ("Ceph", ceph)):
        if candidate.overlaps(existing):
            raise SystemExit(f"{label} subnet {candidate} overlaps host/LXD subnet {existing}")
PY
    then
        log_error "Dedicated network subnet conflicts with existing host state"
        exit 1
    fi
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
if [[ -z "${RESOURCE_NAMESPACE}" ]]; then
    RESOURCE_NAMESPACE=$(printf '%s' "${WORKSPACE_NAME}" | sha256sum | cut -c1-8)
fi
if ! [[ "${RESOURCE_NAMESPACE}" =~ ^[0-9a-f]{8}$ ]]; then
    log_error "resource-namespace must contain exactly eight lowercase hex characters"
    exit 1
fi

print_section "MicroCloud Deployment — scenario: ${SCENARIO}"

detect_lxd_defaults
validate_plane_subnet_availability

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
print_kv "Network mode" "${NETWORK_MODE}"
if [[ "${NETWORK_MODE}" == "fully-segregated-4nic" ]]; then
    print_kv "OVN underlay" "${OVN_UNDERLAY_CIDR}"
    print_kv "Ceph public/internal" "${CEPH_NETWORK_CIDR}"
fi
print_kv "Cluster NIC" "${NETWORK_INTERFACE:-auto-detect}"
print_kv "OVN uplink NIC" "${OVN_UPLINK_INTERFACE:-auto-detect}"
print_kv "Ceph OSD disk" "${CEPH_OSD_DISK:-auto-detect}"
print_kv "Workspace" "${WORKSPACE_NAME}"
print_kv "Resource namespace" "${RESOURCE_NAMESPACE}"
echo ""

# --- OpenTofu provisioning ---
cd "${TERRAFORM_DIR}"
tofu init -input=false >/dev/null 2>&1

STALE_WORKSPACE=false
if tofu workspace list | tr -d '* ' | grep -qx "${WORKSPACE_NAME}"; then
    set +e
    EXISTING_STATE=$(TF_WORKSPACE="${WORKSPACE_NAME}" tofu state list 2>&1)
    STATE_RC=$?
    set -e
    if [[ "${STATE_RC}" -eq 0 && -n "$(printf '%s' "${EXISTING_STATE}" | tr -d '[:space:]')" ]]; then
        log_error "Workspace '${WORKSPACE_NAME}' already contains managed resources. Refusing a fresh deploy that could resize or destroy existing members."
        log_error "Use the supported add/scale workflow, or delete the environment before redeploying."
        exit 1
    fi
    if [[ "${STATE_RC}" -ne 0 && "${EXISTING_STATE}" != *"No state file was found"* ]]; then
        log_error "Workspace '${WORKSPACE_NAME}' exists, but its state could not be inspected safely"
        exit 1
    fi
    STALE_WORKSPACE=true
fi

LXD_PREFIX="${WORKSPACE_NAME//_/-}"
EXPECTED_PROFILES=("${LXD_PREFIX}-iac-base")
EXPECTED_NETWORKS=("ca-${RESOURCE_NAMESPACE}-up")
if [[ "${NETWORK_MODE}" == "fully-segregated-4nic" ]]; then
    EXPECTED_NETWORKS+=(
        "ca-${RESOURCE_NAMESPACE}-ov"
        "ca-${RESOURCE_NAMESPACE}-ce"
    )
fi
EXPECTED_INSTANCES=()
EXPECTED_VOLUMES=()
for i in $(seq 1 "${NODES}"); do
    EXPECTED_INSTANCES+=("${LXD_PREFIX}-node-${i}")
    for disk in $(seq 1 "${CEPH_DISKS_PER_NODE}"); do
        EXPECTED_VOLUMES+=("${LXD_PREFIX}-ceph-${i}-${disk}")
    done
    if [[ "${LOCAL_DISK_GIB}" -gt 0 ]]; then
        EXPECTED_VOLUMES+=("${LXD_PREFIX}-local-${i}")
    fi
done

collect_existing_lxd_names() {
    local instance_rc=0
    local profile_rc=0
    local network_rc=0
    local volume_rc=0
    set +e
    EXISTING_INSTANCES=$(lxc --project default list --format csv -c n 2>&1)
    instance_rc=$?
    EXISTING_PROFILES=$(lxc --project default profile list --format csv -c n 2>&1)
    profile_rc=$?
    EXISTING_NETWORKS=$(lxc --project default network list --format csv -c n 2>&1)
    network_rc=$?
    EXISTING_VOLUMES=$(lxc --project default storage volume list \
        "${TF_VAR_lxd_storage_pool}" --format csv 2>&1)
    volume_rc=$?
    set -e
    if [[ "${instance_rc}" -ne 0 || "${profile_rc}" -ne 0 \
            || "${network_rc}" -ne 0 || "${volume_rc}" -ne 0 ]]; then
        log_error "Could not inspect the complete default-project LXD namespace"
        [[ "${instance_rc}" -ne 0 ]] && log_error "instances: ${EXISTING_INSTANCES}"
        [[ "${profile_rc}" -ne 0 ]] && log_error "profiles: ${EXISTING_PROFILES}"
        [[ "${network_rc}" -ne 0 ]] && log_error "networks: ${EXISTING_NETWORKS}"
        [[ "${volume_rc}" -ne 0 ]] && log_error "volumes: ${EXISTING_VOLUMES}"
        exit 1
    fi
    EXISTING_VOLUMES=$(printf '%s\n' "${EXISTING_VOLUMES}" |
        awk -F',' '$1 == "custom" {print $2}')
}

assert_lxd_names_available() {
    local -a conflicts=()
    collect_existing_lxd_names
    for name in "${EXPECTED_PROFILES[@]}"; do
        grep -Fxq "${name}" <<< "${EXISTING_PROFILES}" \
            && conflicts+=("profile:${name}")
    done
    for name in "${EXPECTED_NETWORKS[@]}"; do
        grep -Fxq "${name}" <<< "${EXISTING_NETWORKS}" \
            && conflicts+=("network:${name}")
    done
    for name in "${EXPECTED_INSTANCES[@]}"; do
        grep -Fxq "${name}" <<< "${EXISTING_INSTANCES}" \
            && conflicts+=("instance:${name}")
    done
    for name in "${EXPECTED_VOLUMES[@]}"; do
        grep -Fxq "${name}" <<< "${EXISTING_VOLUMES}" \
            && conflicts+=("volume:${name}")
    done
    if [[ "${#conflicts[@]}" -gt 0 ]]; then
        log_error "Requested prefix collides with existing unmanaged or orphaned LXD resources:"
        printf '  - %s\n' "${conflicts[@]}" >&2
        log_error "Choose a different --user-prefix or remove only resources you own."
        exit 1
    fi
}

assert_lxd_names_available

if [[ "${AUTO_APPROVE}" != "true" ]]; then
    read -r -p "Proceed with deployment? [y/N] " confirm
    [[ "${confirm,,}" != "y" ]] && { log_warn "Aborted."; exit 0; }
fi

# The direct script can wait on an interactive prompt, so repeat the exact
# namespace reservation immediately before creating the Terraform workspace.
assert_lxd_names_available

if [[ "${STALE_WORKSPACE}" == "true" ]]; then
    log_warn "Removing empty stale workspace '${WORKSPACE_NAME}' before fresh deployment"
    tofu workspace select default >/dev/null 2>&1
    tofu workspace delete "${WORKSPACE_NAME}" >/dev/null 2>&1
fi
tofu workspace new "${WORKSPACE_NAME}" >/dev/null 2>&1
tofu workspace select "${WORKSPACE_NAME}" >/dev/null 2>&1

log_info "Running tofu apply ..."
APPLY_ARGS=(
    -auto-approve
    -parallelism=1
    -var="user_prefix=${USER_PREFIX}"
    -var="resource_namespace=${RESOURCE_NAMESPACE}"
    -var="microcloud_node_count=${NODES}"
    -var="microcloud_node_cpu=${NODE_CPU}"
    -var="microcloud_node_memory_mb=${NODE_MEMORY_MB}"
    -var="microcloud_root_disk_size_gib=${ROOT_DISK_GIB}"
    -var="microcloud_ceph_disk_size_gib=${CEPH_DISK_GIB}"
    -var="ceph_disks_per_node=${CEPH_DISKS_PER_NODE}"
    -var="local_disk_size_gib=${LOCAL_DISK_GIB}"
    -var="microcloud_network_mode=${NETWORK_MODE}"
    -var="microcloud_ovn_underlay_cidr=${OVN_UNDERLAY_CIDR}"
    -var="microcloud_ceph_network_cidr=${CEPH_NETWORK_CIDR}"
)

run_tofu_apply() {
    local log_file="$1"
    set +e
    tofu apply "${APPLY_ARGS[@]}" 2>&1 | tee "${log_file}"
    local apply_rc=${PIPESTATUS[0]}
    set -e
    return "${apply_rc}"
}

reconcile_network_state() {
    local state_list=""
    local address=""
    local name=""
    local role=""
    local expected_cidr=""
    local owner=""
    local actual_role=""
    local actual_cidr=""
    local actual_type=""
    local -a import_vars=(
        -input=false
        -var="user_prefix=${USER_PREFIX}"
        -var="resource_namespace=${RESOURCE_NAMESPACE}"
        -var="microcloud_node_count=${NODES}"
        -var="microcloud_node_cpu=${NODE_CPU}"
        -var="microcloud_node_memory_mb=${NODE_MEMORY_MB}"
        -var="microcloud_root_disk_size_gib=${ROOT_DISK_GIB}"
        -var="microcloud_ceph_disk_size_gib=${CEPH_DISK_GIB}"
        -var="ceph_disks_per_node=${CEPH_DISKS_PER_NODE}"
        -var="local_disk_size_gib=${LOCAL_DISK_GIB}"
        -var="microcloud_network_mode=${NETWORK_MODE}"
        -var="microcloud_ovn_underlay_cidr=${OVN_UNDERLAY_CIDR}"
        -var="microcloud_ceph_network_cidr=${CEPH_NETWORK_CIDR}"
    )
    local -a network_specs=(
        "lxd_network.ovn_uplink|ca-${RESOURCE_NAMESPACE}-up|ovn-uplink|"
    )
    if [[ "${NETWORK_MODE}" == "fully-segregated-4nic" ]]; then
        network_specs+=(
            "lxd_network.ovn_underlay[0]|ca-${RESOURCE_NAMESPACE}-ov|ovn-underlay|${OVN_UNDERLAY_CIDR}"
            "lxd_network.ceph[0]|ca-${RESOURCE_NAMESPACE}-ce|ceph|${CEPH_NETWORK_CIDR}"
        )
    fi

    state_list=$(tofu state list 2>/dev/null || true)
    for spec in "${network_specs[@]}"; do
        IFS='|' read -r address name role expected_cidr <<< "${spec}"
        grep -Fxq "${address}" <<< "${state_list}" && continue
        lxc --project default network show "${name}" >/dev/null 2>&1 || continue

        owner=$(lxc --project default network get "${name}" \
            user.canonical-ai-lab-assistant.owner 2>/dev/null || true)
        actual_role=$(lxc --project default network get "${name}" \
            user.canonical-ai-lab-assistant.role 2>/dev/null || true)
        actual_cidr=$(lxc --project default network get "${name}" \
            user.canonical-ai-lab-assistant.cidr 2>/dev/null || true)
        actual_type=$(lxc --project default network show "${name}" 2>/dev/null |
            awk -F': ' '$1 == "type" {print $2; exit}')
        if [[ "${owner}" != "${WORKSPACE_NAME}" || "${actual_role}" != "${role}" ]]; then
            log_error "Refusing state reconciliation for '${name}': ownership/role mismatch"
            return 1
        fi
        if [[ "${actual_type}" != "bridge" ]]; then
            log_error "Refusing state reconciliation for '${name}': expected bridge, found ${actual_type:-unknown}"
            return 1
        fi
        if [[ -n "${expected_cidr}" && "${actual_cidr}" != "${expected_cidr}" ]]; then
            log_error "Refusing state reconciliation for '${name}': CIDR mismatch"
            return 1
        fi
        if [[ "$(lxc --project default network get "${name}" ipv4.address)" != "none" \
                || "$(lxc --project default network get "${name}" ipv6.address)" != "none" ]]; then
            log_error "Refusing state reconciliation for '${name}': expected an IP-free bridge"
            return 1
        fi

        log_warn "Importing provider-created network into Terraform state: ${address}"
        if ! tofu import "${import_vars[@]}" "${address}" "${name}"; then
            log_error "Could not import '${name}' into Terraform state"
            return 1
        fi
    done
}

APPLY_LOG=$(mktemp)
trap 'rm -f "${APPLY_LOG}"' EXIT
if ! run_tofu_apply "${APPLY_LOG}"; then
    if grep -q "Missing Resource State After Create" "${APPLY_LOG}"; then
        if grep -Eq 'with lxd_(instance|profile|volume)' "${APPLY_LOG}"; then
            log_error "A non-network LXD resource is missing from Terraform state."
            log_error "Automatic recovery is limited to ownership-checked networks."
            exit 1
        fi
        log_warn "LXD created network resources without Terraform state; reconciling exact owned names..."
        reconcile_network_state
        : > "${APPLY_LOG}"
        run_tofu_apply "${APPLY_LOG}"
    else
        exit 1
    fi
fi

log_success "LXD VMs provisioned"

# --- Ansible bootstrap ---
INVENTORY_FILE="${RUNTIME_DIR}/inventory_${WORKSPACE_NAME}.yaml"

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
