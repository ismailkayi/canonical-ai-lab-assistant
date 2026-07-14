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

command -v flock >/dev/null 2>&1 || { log_error "flock not found"; exit 1; }
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

LXD_NETWORK="lxdbr0"
LXD_STORAGE="default"

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
if [[ -n "${DEPLOYMENT_SPEC_JSON}" && "${DEPLOYMENT_SPEC_JSON}" != "null" ]]; then
    SSH_PUBLIC_KEY=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["ssh_public_key"])' <<< "${DEPLOYMENT_SPEC_JSON}")
    LXD_NETWORK=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["lxd_network_name"])' <<< "${DEPLOYMENT_SPEC_JSON}")
    LXD_STORAGE=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["lxd_storage_pool"])' <<< "${DEPLOYMENT_SPEC_JSON}")
fi

log_info "Running tofu destroy..."
PLAN_FILE=$(mktemp)
trap 'rm -f "${PLAN_FILE}"' EXIT
tofu plan -destroy -input=false -out="${PLAN_FILE}" \
    -var="ssh_public_key=${SSH_PUBLIC_KEY}" \
    -var="lxd_network_name=${LXD_NETWORK}" \
    -var="lxd_storage_pool=${LXD_STORAGE}"
tofu apply -auto-approve "${PLAN_FILE}"

if [[ $? -ne 0 ]]; then
    log_error "Terraform destroy failed"
    exit 1
fi

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
