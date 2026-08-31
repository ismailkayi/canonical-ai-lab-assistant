#!/usr/bin/env bash
# verify_cluster_health.sh — Check the health of a deployed MicroCloud environment
# Usage: verify_cluster_health.sh --workspace=<name>
set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()    { echo -e "${CYAN}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1" >&2; }

WORKSPACE=""

for arg in "$@"; do
    case "${arg}" in
        --workspace=*) WORKSPACE="${arg#*=}" ;;
        *) log_error "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

if [[ -z "${WORKSPACE}" ]]; then
    log_error "Usage: $0 --workspace=<name>"
    exit 1
fi

# Derive node prefix (workspace name has underscores, LXD names use dashes)
LXD_PREFIX="${WORKSPACE//_/-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="${SCRIPT_DIR}/../terraform"

# Find the initiator node (first node in workspace)
INITIATOR_NODE="${LXD_PREFIX}-node-1"

if ! lxc info "${INITIATOR_NODE}" &>/dev/null; then
    log_error "Node '${INITIATOR_NODE}' not found. Is workspace '${WORKSPACE}' deployed?"
    exit 1
fi

echo ""
echo "============================================================"
echo "  MicroCloud Health Check — ${WORKSPACE}"
echo "============================================================"
echo ""

run_on_node() {
    local node="$1"; shift
    lxc exec "${node}" -- bash -c "$*" 2>/dev/null || echo "(command failed)"
}

deployment_spec_value() {
    local spec_json="$1"
    local key="$2"
    python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' \
        "${key}" <<< "${spec_json}"
}

cidr_host_address() {
    local cidr="$1"
    local offset="$2"
    python3 - "${cidr}" "${offset}" <<'PY'
import ipaddress
import sys

print(ipaddress.ip_network(sys.argv[1], strict=True)[int(sys.argv[2])])
PY
}

OVERALL_OK=true

# ---- 1. MicroCloud cluster ----
echo "--- MicroCloud cluster ---"
MC_LIST=$(run_on_node "${INITIATOR_NODE}" "microcloud cluster list 2>&1 || echo ERROR")
echo "${MC_LIST}"
if echo "${MC_LIST}" | grep -qi "ERROR\|failed\|unreachable\|OFFLINE\|EVACUATED\|(command failed)"; then
    log_warn "MicroCloud cluster may have issues"
    OVERALL_OK=false
else
    log_success "MicroCloud cluster OK"
fi
echo ""

# ---- 2. LXD cluster ----
echo "--- LXD cluster ---"
LXD_LIST=$(run_on_node "${INITIATOR_NODE}" "lxc cluster list 2>&1 || echo ERROR")
echo "${LXD_LIST}"
if echo "${LXD_LIST}" | grep -qi "ERROR\|Offline\|Evacuated\|(command failed)"; then
    log_warn "LXD cluster has offline or evacuated members"
    OVERALL_OK=false
else
    log_success "LXD cluster OK"
fi
echo ""

# ---- 3. MicroCeph cluster ----
echo "--- MicroCeph cluster ---"
CEPH_LIST=$(run_on_node "${INITIATOR_NODE}" "microceph cluster list 2>&1 || echo ERROR")
echo "${CEPH_LIST}"
CEPH_STATUS=$(run_on_node "${INITIATOR_NODE}" "microceph.ceph -s 2>&1 || echo ERROR")
echo "${CEPH_STATUS}"
if echo "${CEPH_LIST}" | grep -qi "ERROR\|OFFLINE\|(command failed)"; then
    log_warn "MicroCeph cluster may have issues"
    OVERALL_OK=false
elif echo "${CEPH_STATUS}" | grep -qi "HEALTH_ERR"; then
    log_warn "Ceph cluster status: HEALTH_ERR"
    OVERALL_OK=false
elif echo "${CEPH_STATUS}" | grep -qi "HEALTH_WARN"; then
    log_warn "Ceph cluster status: HEALTH_WARN"
    OVERALL_OK=false
elif ! echo "${CEPH_STATUS}" | grep -qi "HEALTH_OK"; then
    log_warn "Ceph health status could not be confirmed"
    OVERALL_OK=false
else
    log_success "MicroCeph cluster OK"
fi
echo ""

# ---- 4. MicroOVN cluster ----
echo "--- MicroOVN cluster ---"
OVN_LIST=$(run_on_node "${INITIATOR_NODE}" "microovn cluster list 2>&1 || echo ERROR")
echo "${OVN_LIST}"
if echo "${OVN_LIST}" | grep -qi "ERROR\|OFFLINE\|(command failed)"; then
    log_warn "MicroOVN cluster may have issues"
    OVERALL_OK=false
else
    log_success "MicroOVN cluster OK"
fi
echo ""

# ---- 5. LXD storage pools ----
echo "--- LXD storage pools ---"
run_on_node "${INITIATOR_NODE}" "lxc storage list"
echo ""

# ---- 6. LXD networks ----
echo "--- LXD networks ---"
run_on_node "${INITIATOR_NODE}" "lxc network list"
echo ""

# ---- 7. Fully segregated network planes ----
DEPLOYMENT_SPEC_JSON=""
if command -v tofu >/dev/null 2>&1 && [[ -d "${TERRAFORM_DIR}/.terraform" ]]; then
    DEPLOYMENT_SPEC_JSON=$(
        cd "${TERRAFORM_DIR}"
        TF_WORKSPACE="${WORKSPACE}" tofu output -json deployment_spec 2>/dev/null || true
    )
fi

NETWORK_MODE="standard-2nic"
if [[ -n "${DEPLOYMENT_SPEC_JSON}" && "${DEPLOYMENT_SPEC_JSON}" != "null" ]]; then
    NETWORK_MODE=$(deployment_spec_value "${DEPLOYMENT_SPEC_JSON}" network_mode)
    [[ -z "${NETWORK_MODE}" ]] && NETWORK_MODE="standard-2nic"
fi

if [[ "${NETWORK_MODE}" == "fully-segregated-4nic" ]]; then
    echo "--- Fully segregated network planes ---"
    NODE_COUNT=$(deployment_spec_value "${DEPLOYMENT_SPEC_JSON}" node_count)
    OVN_UNDERLAY_CIDR=$(deployment_spec_value "${DEPLOYMENT_SPEC_JSON}" ovn_underlay_cidr)
    CEPH_NETWORK_CIDR=$(deployment_spec_value "${DEPLOYMENT_SPEC_JSON}" ceph_network_cidr)

    for i in $(seq 1 "${NODE_COUNT}"); do
        node="${LXD_PREFIX}-node-${i}"
        if ! lxc info "${node}" >/dev/null 2>&1; then
            log_warn "Missing segregated network member: ${node}"
            OVERALL_OK=false
            continue
        fi

        for iface in mgmt0 ovn-uplink ovn-underlay ceph-general; do
            if ! lxc exec "${node}" -- ip link show dev "${iface}" >/dev/null 2>&1; then
                log_warn "${node}: interface ${iface} is missing"
                OVERALL_OK=false
            fi
        done

        if lxc exec "${node}" -- ip -o address show dev ovn-uplink 2>/dev/null \
                | grep -Eq 'inet6? '; then
            log_warn "${node}: ovn-uplink must remain IP-free"
            OVERALL_OK=false
        fi

        expected_ovn_ip=$(cidr_host_address "${OVN_UNDERLAY_CIDR}" $((i + 9)))
        expected_ceph_ip=$(cidr_host_address "${CEPH_NETWORK_CIDR}" $((i + 9)))
        actual_ovn_ip=$(lxc exec "${node}" -- sh -c \
            "ip -4 -o address show dev ovn-underlay scope global | awk '{split(\$4,a,\"/\"); print a[1]; exit}'" \
            2>/dev/null || true)
        actual_ceph_ip=$(lxc exec "${node}" -- sh -c \
            "ip -4 -o address show dev ceph-general scope global | awk '{split(\$4,a,\"/\"); print a[1]; exit}'" \
            2>/dev/null || true)

        if [[ "${actual_ovn_ip}" != "${expected_ovn_ip}" ]]; then
            log_warn "${node}: OVN underlay IP is ${actual_ovn_ip:-missing}, expected ${expected_ovn_ip}"
            OVERALL_OK=false
        fi
        if [[ "${actual_ceph_ip}" != "${expected_ceph_ip}" ]]; then
            log_warn "${node}: Ceph network IP is ${actual_ceph_ip:-missing}, expected ${expected_ceph_ip}"
            OVERALL_OK=false
        fi
        if ! lxc exec "${node}" -- ip -4 route show default 2>/dev/null \
                | grep -q ' dev mgmt0'; then
            log_warn "${node}: default route is not on mgmt0"
            OVERALL_OK=false
        fi

        # The plane bridges are shared L2 domains. A ring probe validates every
        # member as both source and destination without quadratic checks at 50 nodes.
        peer_index=$((i % NODE_COUNT + 1))
        peer_ovn_ip=$(cidr_host_address "${OVN_UNDERLAY_CIDR}" $((peer_index + 9)))
        peer_ceph_ip=$(cidr_host_address "${CEPH_NETWORK_CIDR}" $((peer_index + 9)))
        if ! lxc exec "${node}" -- ping -I ovn-underlay -c 1 -W 2 "${peer_ovn_ip}" \
                >/dev/null 2>&1; then
            log_warn "${node}: cannot reach ${peer_ovn_ip} over ovn-underlay"
            OVERALL_OK=false
        fi
        if ! lxc exec "${node}" -- ping -I ceph-general -c 1 -W 2 "${peer_ceph_ip}" \
                >/dev/null 2>&1; then
            log_warn "${node}: cannot reach ${peer_ceph_ip} over ceph-general"
            OVERALL_OK=false
        fi
    done

    for option in cluster_network public_network; do
        CEPH_CONFIG=$(run_on_node "${INITIATOR_NODE}" \
            "microceph cluster config get ${option} 2>&1")
        if ! echo "${CEPH_CONFIG}" \
                | grep -Eq "${option}.*${CEPH_NETWORK_CIDR//./\\.}"; then
            log_warn "Ceph ${option} is not ${CEPH_NETWORK_CIDR}"
            OVERALL_OK=false
        fi
    done

    if [[ "${OVERALL_OK}" == "true" ]]; then
        log_success "Four-NIC OVN and Ceph planes are correctly segregated"
    fi
    echo ""
fi

echo "============================================================"
if [[ "${OVERALL_OK}" == "true" ]]; then
    log_success "All services healthy — workspace: ${WORKSPACE}"
else
    log_warn "Some services may need attention — review output above"
    exit 1
fi
