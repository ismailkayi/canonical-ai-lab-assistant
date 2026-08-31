#!/usr/bin/env bash
# cleanup_microcloud.sh — MicroCloud environment teardown via OpenTofu
# Destroys all resources (VMs, networks, volumes) for a given workspace.
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

# -----------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------
WORKSPACE=""
AUTO_APPROVE=false
EXPECTED_STATE_LINEAGE=""
EXPECTED_STATE_SERIAL=""
EXPECTED_CURRENT_NODES=""
EXPECTED_TARGET_NODES=""

# -----------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------
for arg in "$@"; do
    case "${arg}" in
        --workspace=*)  WORKSPACE="${arg#*=}" ;;
        --auto-approve) AUTO_APPROVE=true ;;
        --expected-state-lineage=*) EXPECTED_STATE_LINEAGE="${arg#*=}" ;;
        --expected-state-serial=*) EXPECTED_STATE_SERIAL="${arg#*=}" ;;
        --expected-current-nodes=*) EXPECTED_CURRENT_NODES="${arg#*=}" ;;
        --expected-target-nodes=*) EXPECTED_TARGET_NODES="${arg#*=}" ;;
        *) log_error "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

if [[ -z "${WORKSPACE}" ]]; then
    log_error "Missing required --workspace parameter"
    exit 1
fi
if [[ -z "${EXPECTED_STATE_LINEAGE}" || -z "${EXPECTED_STATE_SERIAL}" \
      || -z "${EXPECTED_CURRENT_NODES}" || -z "${EXPECTED_TARGET_NODES}" ]]; then
    log_error "Missing approval-bound Terraform state identity/count parameters"
    exit 1
fi

for tool in flock lxc tofu; do
    command -v "${tool}" >/dev/null 2>&1 || {
        log_error "${tool} not found"
        exit 1
    }
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
if [[ "${EXPECTED_TARGET_NODES}" != "0" ]]; then
    log_error "Cleanup approval must target zero nodes"
    exit 1
fi

# -----------------------------------------------------------------------
# Locate terraform directory
# -----------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"
TF_DIR="${REPO_ROOT}/terraform"

if [[ ! -d "${TF_DIR}" ]]; then
    log_error "Terraform directory not found: ${TF_DIR}"
    exit 1
fi

print_section "MicroCloud Cleanup — workspace: ${WORKSPACE}"

# -----------------------------------------------------------------------
# Prepare terraform variables
# -----------------------------------------------------------------------
# Terraform destroy requires these variables even though we're destroying state.
# Extract sensible defaults from LXD or use minimal required values.
SSH_KEY="${HOME}/.ssh/id_rsa.pub"
if [[ ! -f "${SSH_KEY}" ]]; then
    SSH_KEY="${HOME}/.ssh/id_rsa_lab.pub"
fi
if [[ ! -f "${SSH_KEY}" ]]; then
    log_warn "SSH public key not found, using placeholder"
    SSH_PUBLIC_KEY="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC placeholder"
else
    SSH_PUBLIC_KEY=$(cat "${SSH_KEY}")
fi

LXD_STORAGE="default"
LXD_PROJECT=""

# -----------------------------------------------------------------------
# Run terraform destroy
# -----------------------------------------------------------------------
cd "${TF_DIR}"

log_info "Initializing Terraform workspace..."
tofu init -upgrade -input=false > /dev/null 2>&1 || true

log_info "Selecting workspace: ${WORKSPACE}"
tofu workspace select "${WORKSPACE}" 2>/dev/null || {
    log_warn "Workspace '${WORKSPACE}' not found or not selected. Listing available workspaces:"
    tofu workspace list
    log_error "Please specify a valid workspace."
    exit 1
}

STATE_IDENTITY=$(tofu state pull)
ACTUAL_STATE_LINEAGE=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["lineage"])' <<< "${STATE_IDENTITY}")
ACTUAL_STATE_SERIAL=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["serial"])' <<< "${STATE_IDENTITY}")
CURRENT_NODES=$(tofu output -json node_names 2>/dev/null \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo "0")
if [[ "${ACTUAL_STATE_LINEAGE}" != "${EXPECTED_STATE_LINEAGE}" \
      || "${ACTUAL_STATE_SERIAL}" != "${EXPECTED_STATE_SERIAL}" \
      || "${CURRENT_NODES}" != "${EXPECTED_CURRENT_NODES}" ]]; then
    log_error "Terraform state identity or node count changed after approval"
    exit 1
fi

DEPLOYMENT_SPEC_JSON=$(tofu output -json deployment_spec 2>/dev/null || true)
if [[ -z "${DEPLOYMENT_SPEC_JSON}" || "${DEPLOYMENT_SPEC_JSON}" == "null" ]]; then
    log_error "Workspace '${WORKSPACE}' has no readable deployment_spec"
    exit 1
fi
spec_value() {
    local key="$1"
    python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' \
        "${key}" <<< "${DEPLOYMENT_SPEC_JSON}"
}

SPEC_SSH_PUBLIC_KEY=$(spec_value ssh_public_key)
SPEC_LXD_STORAGE=$(spec_value lxd_storage_pool)
LXD_PROJECT=$(spec_value lxd_project_name)
MANAGEMENT_NETWORK=$(spec_value management_network)
OVN_UPLINK_NETWORK=$(spec_value ovn_uplink_network)
OVN_UNDERLAY_NETWORK=$(spec_value ovn_underlay_network)
CEPH_NETWORK=$(spec_value ceph_network)

[[ -n "${SPEC_SSH_PUBLIC_KEY}" ]] && SSH_PUBLIC_KEY="${SPEC_SSH_PUBLIC_KEY}"
[[ -n "${SPEC_LXD_STORAGE}" ]] && LXD_STORAGE="${SPEC_LXD_STORAGE}"

PROJECT_MANAGER=$(lxc project get "${LXD_PROJECT}" \
    user.canonical-ai-lab-assistant.managed-by 2>/dev/null || true)
PROJECT_WORKSPACE=$(lxc project get "${LXD_PROJECT}" \
    user.canonical-ai-lab-assistant.workspace 2>/dev/null || true)
if [[ "${PROJECT_MANAGER}" != "canonical-ai-lab-assistant" \
        || "${PROJECT_WORKSPACE}" != "${WORKSPACE}" ]]; then
    log_error "Refusing cleanup: LXD project ownership does not match the approved workspace"
    log_error "project=${LXD_PROJECT}, managed-by=${PROJECT_MANAGER:-unset}, workspace=${PROJECT_WORKSPACE:-unset}"
    exit 1
fi

NETWORKS=(
    "${MANAGEMENT_NETWORK}"
    "${OVN_UPLINK_NETWORK}"
    "${OVN_UNDERLAY_NETWORK}"
    "${CEPH_NETWORK}"
)
for network in "${NETWORKS[@]}"; do
    [[ -z "${network}" ]] && continue
    lxc --project default network show "${network}" >/dev/null 2>&1 || continue
    NETWORK_OWNER=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.owner 2>/dev/null || true)
    NETWORK_PROJECT=$(lxc --project default network get "${network}" \
        user.canonical-ai-lab-assistant.project 2>/dev/null || true)
    if [[ "${NETWORK_OWNER}" != "${WORKSPACE}" \
            || "${NETWORK_PROJECT}" != "${LXD_PROJECT}" ]]; then
        log_error "Refusing cleanup: network '${network}' ownership does not match"
        log_error "owner=${NETWORK_OWNER:-unset}, project=${NETWORK_PROJECT:-unset}"
        exit 1
    fi
done

log_info "Running tofu destroy..."
PLAN_FILE=$(mktemp)
trap 'rm -f "${PLAN_FILE}"' EXIT
tofu plan -destroy -input=false -out="${PLAN_FILE}" \
    -var="ssh_public_key=${SSH_PUBLIC_KEY}" \
    -var="lxd_project_name=${LXD_PROJECT}" \
    -var="management_network_name=${MANAGEMENT_NETWORK}" \
    -var="ovn_uplink_network_name=${OVN_UPLINK_NETWORK}" \
    -var="ovn_underlay_network_name=${OVN_UNDERLAY_NETWORK}" \
    -var="ceph_network_name=${CEPH_NETWORK}" \
    -var="lxd_storage_pool=${LXD_STORAGE}"
tofu apply -auto-approve "${PLAN_FILE}"

if [[ $? -ne 0 ]]; then
    log_error "Terraform destroy failed"
    exit 1
fi

for network in "${NETWORKS[@]}"; do
    [[ -z "${network}" ]] && continue
    if lxc --project default network show "${network}" >/dev/null 2>&1; then
        NETWORK_OWNER=$(lxc --project default network get "${network}" \
            user.canonical-ai-lab-assistant.owner 2>/dev/null || true)
        NETWORK_PROJECT=$(lxc --project default network get "${network}" \
            user.canonical-ai-lab-assistant.project 2>/dev/null || true)
        if [[ "${NETWORK_OWNER}" != "${WORKSPACE}" \
                || "${NETWORK_PROJECT}" != "${LXD_PROJECT}" ]]; then
            log_error "Network '${network}' ownership changed during cleanup; refusing deletion"
            log_error "owner=${NETWORK_OWNER:-unset}, project=${NETWORK_PROJECT:-unset}"
            exit 1
        fi
        if lxc --project default network delete "${network}" >/dev/null 2>&1; then
            log_info "Removed owned global LXD network: ${network}"
        else
            log_warn "Could not remove owned network ${network}; lab-ai orphans will report it"
        fi
    fi
done

# -----------------------------------------------------------------------
# Delete the workspace after successful destroy
# -----------------------------------------------------------------------
log_info "Removing Terraform workspace: ${WORKSPACE}"
tofu workspace select default 2>/dev/null || true
tofu workspace delete "${WORKSPACE}" 2>/dev/null || {
    log_error "Could not delete workspace '${WORKSPACE}' after destroying resources"
    exit 1
}

# -----------------------------------------------------------------------
# Clean up inventory file if it exists
# -----------------------------------------------------------------------
REPO_DIR="$(dirname "${TF_DIR}")"
INVENTORY_FILE="${REPO_DIR}/inventory_${WORKSPACE}.yaml"
if [[ -f "${INVENTORY_FILE}" ]]; then
    log_info "Removing inventory file: ${INVENTORY_FILE}"
    rm -f "${INVENTORY_FILE}"
fi

# -----------------------------------------------------------------------
# Final summary
# -----------------------------------------------------------------------
print_section "Cleanup Complete"
log_success "All resources for workspace '${WORKSPACE}' have been destroyed."
log_info "The environment is now clean and ready for new deployments."
