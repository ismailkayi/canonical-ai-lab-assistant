#!/usr/bin/env bash
# STEP 2 — Show the cluster members as ONE pool of resources, then provision
# INSTANCE_COUNT instances from that pool. MicroCloud decides where each one runs.
source "$(dirname "$0")/common.sh"
# --print: show the commands to type by hand instead of running them.
guide() {
    local type_flag=""
    [[ "${INSTANCE_TYPE}" != "vm" ]] || type_flag=" --vm"
    print_guide <<EOF
# ============================================================================
# STEP 2 by hand: one resource pool, ${INSTANCE_COUNT} instances from it
# ============================================================================

# Open a shell on the first cluster member. Everything below runs inside it,
# and its lxc command talks to the WHOLE cluster, not just this one VM.
lxc shell ${LEADER}

# 1/3  The pool: every member, and what each one contributes.
lxc cluster list
lxc info --resources --target ${NODE_PREFIX}-2   # one member's CPUs, memory and disks
lxc storage info remote                       # the shared Ceph pool, the sum of all members

# 2/3  Ask the pool for ${INSTANCE_COUNT} instances. There is no --target: we do not say where they run.
for i in \$(seq 1 ${INSTANCE_COUNT}); do
  lxc launch ${UBUNTU_IMAGE} ${INSTANCE_PREFIX}-\$i${type_flag} -c limits.cpu=${INSTANCE_CPU} -c limits.memory=${INSTANCE_MEMORY}
done

# 3/3  Where did they land? Look at the LOCATION column: the cloud chose.
lxc list -c ns4tL

# Leave the member's shell when you are done:
exit
EOF
}
[[ "${1:-}" != "--print" ]] || { guide; exit 0; }
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
