#!/usr/bin/env bash
# STEP 1 — Create NODE_COUNT VMs on this host and join them into one MicroCloud.
# Safe to re-run: existing VMs are kept, and an existing cluster is just shown.
source "$(dirname "$0")/common.sh"
require_lxd

cluster_ready() { lxc info "${LEADER}" >/dev/null 2>&1 && mc lxc cluster list >/dev/null 2>&1; }

if cluster_ready; then
    ok "MicroCloud is already built — showing it."
    show lxc list "${NODE_PREFIX}-" -c ns4tL
    show lxc exec "${LEADER}" -- lxc cluster list
    exit 0
fi

POOL=$(lxc profile device get default root pool 2>/dev/null || echo default)

# --- 1. Uplink network for MicroCloud's software-defined network (OVN) ------
title "1/4  Uplink network '${UPLINK_BRIDGE}'"
if lxc network show "${UPLINK_BRIDGE}" >/dev/null 2>&1; then
    ok "Already exists"
else
    # No DHCP: MicroCloud's OVN router takes addresses from this range itself.
    show lxc network create "${UPLINK_BRIDGE}" \
        ipv4.address="${UPLINK_SUBNET}.1/24" ipv4.nat=true ipv4.dhcp=false ipv6.address=none
fi

# --- 2. The cluster-member VMs --------------------------------------------
title "2/4  Creating ${NODE_COUNT} VMs (${NODE_CPU} vCPU, ${NODE_MEMORY} RAM, ${NODE_CEPH_DISK} Ceph disk each)"
for i in $(seq 1 "${NODE_COUNT}"); do
    node=$(node_name "$i")
    if lxc info "${node}" >/dev/null 2>&1; then
        ok "${node} already exists"
        continue
    fi
    lxc storage volume show "${POOL}" "${node}-ceph" >/dev/null 2>&1 \
        || lxc storage volume create "${POOL}" "${node}-ceph" --type=block size="${NODE_CEPH_DISK}" >/dev/null
    lxc init "${UBUNTU_IMAGE}" "${node}" --vm \
        -c limits.cpu="${NODE_CPU}" -c limits.memory="${NODE_MEMORY}" \
        -d root,size="${NODE_ROOT_DISK}" >/dev/null
    lxc config device add "${node}" uplink nic network="${UPLINK_BRIDGE}" >/dev/null
    lxc config device add "${node}" ceph disk pool="${POOL}" source="${node}-ceph" >/dev/null
    lxc start "${node}"
    ok "${node} created"
done

info "Waiting for all VMs to boot ..."
for i in $(seq 1 "${NODE_COUNT}"); do
    node=$(node_name "$i")
    wait_for 600 "${node} to boot" lxc exec "${node}" -- true
    wait_for 600 "${node} to finish first boot" lxc exec "${node}" -- snap wait system seed.loaded
done
show lxc list "${NODE_PREFIX}-" -c ns4tL

# --- 3. Install MicroCloud software on every VM (in parallel) -------------
title "3/4  Installing MicroCloud (LXD + MicroCeph + MicroOVN + MicroCloud) on every VM"
install_snaps() {
    local node="$1"
    lxc exec "${node}" -- sh -ec '
        snap install lxd --channel=5.21/stable 2>/dev/null || snap refresh lxd --channel=5.21/stable
        snap install microceph --channel=squid/stable
        snap install microovn --channel=24.03/stable
        snap install microcloud --channel=2/stable
        snap refresh --hold lxd microceph microovn microcloud   # no surprise updates mid-demo
    '
}
pids=()
for i in $(seq 1 "${NODE_COUNT}"); do
    node=$(node_name "$i")
    install_snaps "${node}" >"${LOG_DIR}/${node}-install.log" 2>&1 &
    pids+=("$!")
done
for idx in "${!pids[@]}"; do
    node=$(node_name $((idx + 1)))
    wait "${pids[$idx]}" || die "Install failed on ${node} — see logs/${node}-install.log"
    ok "${node}"
done

# --- 4. Form the cluster ---------------------------------------------------
title "4/4  Joining ${NODE_COUNT} VMs into one MicroCloud"

# Each VM: interface on the default network (cluster traffic), a spare
# interface with no IP (OVN uplink), and the dedicated Ceph disk.
node_facts() {
    lxc exec "$1" -- sh -ec '
        primary=$(ip -4 route show default | awk "{print \$5; exit}")
        uplink=""
        for nic in $(ls /sys/class/net); do
            [ "$nic" = lo ] || [ "$nic" = "$primary" ] && continue
            ip -4 addr show "$nic" | grep -q inet || { uplink="$nic"; break; }
        done
        disk=$(lsblk -dn -o NAME,SERIAL | awk "\$2 == \"lxd_ceph\" {print \"/dev/\" \$1; exit}")
        cidr=$(ip -4 -o addr show dev "$primary" scope global | awk "{print \$4; exit}")
        echo "$uplink $disk $cidr"
    '
}

lookup_subnet=""
systems=""
for i in $(seq 1 "${NODE_COUNT}"); do
    node=$(node_name "$i")
    read -r uplink disk cidr < <(node_facts "${node}")
    [[ -n "${uplink}" && -n "${disk}" ]] || die "Could not find uplink NIC / Ceph disk on ${node}"
    [[ -n "${lookup_subnet}" ]] || lookup_subnet=$(python3 -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" "${cidr}")
    systems+="  - name: ${node}
    ovn_uplink_interface: ${uplink}
    storage:
      ceph:
        - path: ${disk}
          wipe: true
"
done

PRESEED="${LOG_DIR}/microcloud-preseed.yaml"
cat >"${PRESEED}" <<EOF
lookup_subnet: ${lookup_subnet}
initiator: ${LEADER}
session_passphrase: microcloud-demo-$(date +%s)
systems:
${systems}ovn:
  ipv4_gateway: ${UPLINK_SUBNET}.1/24
  ipv4_range: ${UPLINK_SUBNET}.100-${UPLINK_SUBNET}.254
  dns_servers: 8.8.8.8
EOF
info "Cluster definition written to logs/microcloud-preseed.yaml"

# The same definition is handed to every VM; the joiners wait for the initiator.
pids=()
for i in $(seq 2 "${NODE_COUNT}"); do
    node=$(node_name "$i")
    lxc file push "${PRESEED}" "${node}/root/preseed.yaml" >/dev/null
    lxc exec "${node}" -- sh -c 'microcloud preseed < /root/preseed.yaml' >"${LOG_DIR}/${node}-join.log" 2>&1 &
    pids+=("$!")
done
lxc file push "${PRESEED}" "${LEADER}/root/preseed.yaml" >/dev/null
show lxc exec "${LEADER}" -- sh -c 'microcloud preseed < /root/preseed.yaml'
for idx in "${!pids[@]}"; do
    node=$(node_name $((idx + 2)))
    wait "${pids[$idx]}" || die "${node} failed to join — see logs/${node}-join.log"
done

# Safety net: make sure the instance network exists.
if ! mc lxc network show default >/dev/null 2>&1; then
    warn "Creating the OVN network that MicroCloud did not create"
    mc lxc network create default --type=ovn network=UPLINK ipv6.address=none
fi

# Ceph OSDs aim to cache up to 4 GiB each by default; on small members that
# would starve everything else. 1 GiB is plenty for a demo-sized cluster.
mc microceph.ceph config set osd osd_memory_target 1073741824 >/dev/null 2>&1 \
    || warn "Could not lower the Ceph OSD memory target (continuing)"

echo
ok "MicroCloud is up: ${NODE_COUNT} machines, one cloud."
show lxc exec "${LEADER}" -- lxc cluster list
