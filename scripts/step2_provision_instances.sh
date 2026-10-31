#!/usr/bin/env bash
# STEP 2 — Show the 10 machines as ONE pool of resources, then provision
# 5 instances from that pool. MicroCloud decides where each one runs.
source "$(dirname "$0")/common.sh"
require_cluster

# --- 1. The pool -----------------------------------------------------------
title "1/3  ${NODE_COUNT} machines, one resource pool"
show lxc exec "${LEADER}" -- lxc cluster list -c nurs

mc sh -c '
    for m in $(lxc cluster list -f csv -c n); do
        printf "%s " "$m"
        lxc query "/1.0/resources?target=$m" | tr -d "\n"
        echo
    done
' | python3 "${REPO_ROOT}/scripts/pool_summary.py"
show lxc exec "${LEADER}" -- lxc storage info remote

# --- 2. Provision from the pool ------------------------------------------
title "2/3  Provisioning ${INSTANCE_COUNT} instances (${INSTANCE_CPU} vCPU, ${INSTANCE_MEMORY} RAM each)"

type_flag="--vm"
if [[ "${INSTANCE_TYPE}" != "vm" ]]; then
    type_flag=""
elif ! mc test -c /dev/kvm; then
    warn "Nested virtualization is not available inside the cluster members — using system containers instead."
    type_flag=""
fi

for i in $(seq 1 "${INSTANCE_COUNT}"); do
    inst=$(instance_name "$i")
    if mc lxc info "${inst}" >/dev/null 2>&1; then
        ok "${inst} already exists"
        continue
    fi
    # No --target: the cluster scheduler picks the member with the most room.
    show lxc exec "${LEADER}" -- lxc launch "${UBUNTU_IMAGE}" "${inst}" ${type_flag} \
        -c limits.cpu="${INSTANCE_CPU}" -c limits.memory="${INSTANCE_MEMORY}"
done

info "Waiting for the instances to boot and get network ..."
for i in $(seq 1 "${INSTANCE_COUNT}"); do
    inst=$(instance_name "$i")
    wait_for 600 "${inst} to boot" in_instance "${inst}" true
    in_instance "${inst}" cloud-init status --wait >/dev/null 2>&1 || true
done

# --- 3. Result -------------------------------------------------------------
title "3/3  Where did they land?"
show lxc exec "${LEADER}" -- lxc list -c ns4tL
echo
ok "${INSTANCE_COUNT} instances provisioned on demand, placed automatically across the pool,"
ok "with disks on shared Ceph storage and networking on OVN."
