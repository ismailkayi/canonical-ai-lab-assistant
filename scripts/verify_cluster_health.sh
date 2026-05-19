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

OVERALL_OK=true

# ---- 1. MicroCloud cluster ----
echo "--- MicroCloud cluster ---"
MC_LIST=$(run_on_node "${INITIATOR_NODE}" "microcloud cluster list 2>&1 || echo ERROR")
echo "${MC_LIST}"
if echo "${MC_LIST}" | grep -qi "ERROR\|failed\|unreachable"; then
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
if echo "${LXD_LIST}" | grep -qi "ERROR\|Offline\|Evacuated"; then
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
if echo "${CEPH_LIST}" | grep -qi "ERROR"; then
    log_warn "MicroCeph cluster may have issues"
    OVERALL_OK=false
elif echo "${CEPH_STATUS}" | grep -qi "HEALTH_ERR"; then
    log_warn "Ceph cluster status: HEALTH_ERR"
    OVERALL_OK=false
elif echo "${CEPH_STATUS}" | grep -qi "HEALTH_WARN"; then
    log_warn "Ceph cluster status: HEALTH_WARN (may be transient)"
else
    log_success "MicroCeph cluster OK"
fi
echo ""

# ---- 4. MicroOVN cluster ----
echo "--- MicroOVN cluster ---"
OVN_LIST=$(run_on_node "${INITIATOR_NODE}" "microovn cluster list 2>&1 || echo ERROR")
echo "${OVN_LIST}"
if echo "${OVN_LIST}" | grep -qi "ERROR"; then
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

echo "============================================================"
if [[ "${OVERALL_OK}" == "true" ]]; then
    log_success "All services healthy — workspace: ${WORKSPACE}"
else
    log_warn "Some services may need attention — review output above"
    exit 1
fi
