#!/usr/bin/env bash
# One-time host preparation: install and initialise LXD.
source "$(dirname "$0")/common.sh"

title "Preparing this machine for the demo"

if ! command -v snap >/dev/null; then
    show sudo apt-get update
    show sudo apt-get install -y snapd
fi

if snap list lxd >/dev/null 2>&1; then
    ok "LXD is already installed"
else
    show sudo snap install lxd --channel=5.21/stable
fi

if ! id -nG "${USER}" | grep -qw lxd; then
    show sudo usermod -aG lxd "${USER}"
    NEED_RELOGIN=true
fi

if sudo lxc storage list --format csv 2>/dev/null | grep -q .; then
    ok "LXD is already initialised"
else
    show sudo lxd init --auto
fi

echo
ok "Host is ready."
if [[ "${NEED_RELOGIN:-false}" == "true" ]]; then
    warn "You were added to the 'lxd' group. Log out and back in (or reconnect SSH), then run: ./demo.py check"
else
    info "Next: ./demo.py check"
fi
