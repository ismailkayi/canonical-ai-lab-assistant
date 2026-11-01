#!/usr/bin/env bash
# Show what exists right now. Read-only.
source "$(dirname "$0")/common.sh"
require_lxd

title "Cluster-member VMs on this host"
lxc list "${NODE_PREFIX}-" -c ns4t

if lxc info "${LEADER}" >/dev/null 2>&1 && mc lxc cluster list >/dev/null 2>&1; then
    title "MicroCloud cluster"
    mc lxc cluster list
    title "Instances running on MicroCloud"
    mc lxc list -c ns4tL
else
    warn "MicroCloud is not running (step 1 not done yet)."
fi
