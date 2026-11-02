#!/usr/bin/env bash
# Delete everything the demo created on this host (VMs, disks, uplink network).
source "$(dirname "$0")/common.sh"
require_lxd

POOL=$(lxc profile device get default root pool 2>/dev/null || echo default)
mapfile -t nodes < <(lxc list "${NODE_PREFIX}-" -f csv -c n)

title "This will permanently delete:"
printf '  VM:      %s\n' "${nodes[@]:-(none)}"
echo   "  Disks:   ${NODE_PREFIX}-*-ceph in pool '${POOL}'"
echo   "  Network: ${UPLINK_BRIDGE}"
echo   "  ...and the instances inside MicroCloud along with them."
read -r -p "Type 'delete' to continue: " answer
[[ "${answer}" == "delete" ]] || die "Cancelled — nothing was deleted."

for node in "${nodes[@]}"; do
    show lxc delete --force "${node}"
done
for vol in $(lxc storage volume list "${POOL}" -f csv -c n 2>/dev/null | grep "^${NODE_PREFIX}-.*-ceph$" || true); do
    show lxc storage volume delete "${POOL}" "${vol}"
done
if lxc network show "${UPLINK_BRIDGE}" >/dev/null 2>&1; then
    show lxc network delete "${UPLINK_BRIDGE}"
fi
rm -rf "${LOG_DIR:?}"/* "${REPORT_DIR:?}"/*
ok "Clean. Remember to remove the MC-Instance_* computers in Landscape too."
info "Kept for next time: cache/ (downloaded snaps) and ui-access/ (browser login certificate)."
