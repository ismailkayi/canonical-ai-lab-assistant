#!/usr/bin/env bash
# STEP 2 — Show the cluster members as ONE pool of resources, then provision
# INSTANCE_COUNT instances from that pool. MicroCloud decides where each one runs.
source "$(dirname "$0")/common.sh"
require_cluster

# --- 1. The pool -----------------------------------------------------------
title "1/3  ${NODE_COUNT} machines, one resource pool"
show lxc exec "${LEADER}" -- lxc cluster list

# Each member's hardware, plus every instance with where it runs and its
# limits, so the table shows how much of the pool is already handed out.
pool_report() {
    mc sh -c '
        # Member names from the API ("/1.0/cluster/members/<name>"), one per line.
        for m in $(lxc query /1.0/cluster/members | sed -n "s|.*/1.0/cluster/members/\([^\"]*\)\".*|\1|p"); do
            printf "%s " "$m"
            lxc query "/1.0/resources?target=$m" | tr -d "\n"
            echo
        done
        printf "@instances "
        lxc query "/1.0/instances?recursion=1" | tr -d "\n"
        echo
    ' | python3 "${REPO_ROOT}/scripts/pool_summary.py"
}
pool_report
show lxc exec "${LEADER}" -- lxc storage info remote

# --- 2. Provision from the pool ------------------------------------------
title "2/3  Provisioning $(count "${INSTANCE_COUNT}" instance) (${INSTANCE_CPU} vCPU, ${INSTANCE_MEMORY} RAM each)"

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
    # </dev/null: "lxc launch" reads optional YAML from stdin when it is not a
    # terminal, and would otherwise wait forever.
    show lxc exec "${LEADER}" -- lxc launch "${UBUNTU_IMAGE}" "${inst}" ${type_flag} \
        -c limits.cpu="${INSTANCE_CPU}" -c limits.memory="${INSTANCE_MEMORY}" </dev/null
done

info "Waiting for $(count "${INSTANCE_COUNT}" instance) to boot and get network ..."
for i in $(seq 1 "${INSTANCE_COUNT}"); do
    inst=$(instance_name "$i")
    wait_for 600 "${inst} to boot" in_instance "${inst}" true
    in_instance "${inst}" cloud-init status --wait >/dev/null 2>&1 || true
done

# --- 3. Result -------------------------------------------------------------
title "3/3  Where did they land, and what did they take from the pool?"
show lxc exec "${LEADER}" -- lxc list -c ns4tL
pool_report
echo
ok "$(count "${INSTANCE_COUNT}" instance) provisioned on demand, placed automatically by the cluster,"
ok "with disks on shared Ceph storage and networking on OVN."
bash "${REPO_ROOT}/scripts/ui_links.sh" microcloud
