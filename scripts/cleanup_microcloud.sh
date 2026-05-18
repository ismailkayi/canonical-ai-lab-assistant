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

# -----------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------
for arg in "$@"; do
    case "${arg}" in
        --workspace=*)  WORKSPACE="${arg#*=}" ;;
        --auto-approve) AUTO_APPROVE=true ;;
        *) log_error "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

if [[ -z "${WORKSPACE}" ]]; then
    log_error "Missing required --workspace parameter"
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

log_info "Running tofu destroy..."
if [[ "${AUTO_APPROVE}" == "true" ]]; then
    tofu destroy -auto-approve -input=false \
        -var="ssh_public_key=${SSH_PUBLIC_KEY}" \
        -var="lxd_network_name=${LXD_NETWORK}" \
        -var="lxd_storage_pool=${LXD_STORAGE}"
else
    tofu destroy -input=true \
        -var="ssh_public_key=${SSH_PUBLIC_KEY}" \
        -var="lxd_network_name=${LXD_NETWORK}" \
        -var="lxd_storage_pool=${LXD_STORAGE}"
fi

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
    log_warn "Could not delete workspace '${WORKSPACE}' (may still have state). Continuing."
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
