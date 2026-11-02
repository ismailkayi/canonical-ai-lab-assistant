#!/usr/bin/env bash
# Print the Canonical web UIs for the demo, ready to open in a browser:
#   ui_links.sh microcloud   MicroCloud / LXD UI (sets up browser access once)
#   ui_links.sh landscape    Landscape
#   ui_links.sh usg          USG reports and the Ubuntu Pro dashboard
#   ui_links.sh all          everything (default)
# Every link is printed on its own line as "  <label>  <url>", so demo.py can
# offer to open them.
source "$(dirname "$0")/common.sh"
TRACE_COMMANDS=no
what="${1:-all}"

UI_DIR="${REPO_ROOT}/ui-access"
UI_CERT="${UI_DIR}/microcloud-demo-ui.crt"
UI_KEY="${UI_DIR}/microcloud-demo-ui.key"
UI_PFX="${UI_DIR}/microcloud-demo-ui.pfx"
UI_PFX_PASSWORD="microcloud"

link() { printf "  %-28s ${BOLD}%s${NC}\n" "$1" "$2"; }

# --- MicroCloud / LXD -------------------------------------------------------
# MicroCloud's web UI is the LXD UI, served by every cluster member on port
# 8443. It shows the cluster members, the instances, the Ceph storage pool and
# the OVN networks. The browser logs in with a client certificate, which we
# create once and add to the cluster's trust store.
microcloud_links() {
    title "MicroCloud / LXD web UI"
    if ! { lxc info "${LEADER}" >/dev/null 2>&1 && mc lxc cluster list >/dev/null 2>&1; }; then
        warn "MicroCloud is not running yet (run step 1 first)."
        return 0
    fi
    type -P openssl >/dev/null || { warn "openssl is not installed: sudo apt install openssl"; return 0; }

    mkdir -p "${UI_DIR}"
    if [[ ! -s "${UI_CERT}" || ! -s "${UI_PFX}" ]]; then
        openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
            -keyout "${UI_KEY}" -out "${UI_CERT}" -subj "/CN=microcloud-demo-ui" 2>/dev/null
        openssl pkcs12 -export -inkey "${UI_KEY}" -in "${UI_CERT}" -out "${UI_PFX}" \
            -name "MicroCloud demo UI" -passout "pass:${UI_PFX_PASSWORD}"
    fi
    local fp
    fp=$(openssl x509 -in "${UI_CERT}" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d : | tr 'A-F' 'a-f')
    if ! mc lxc query "/1.0/certificates/${fp}" >/dev/null 2>&1; then
        lxc file push "${UI_CERT}" "${LEADER}/root/microcloud-demo-ui.crt" >/dev/null
        show lxc exec "${LEADER}" -- lxc config trust add /root/microcloud-demo-ui.crt --name microcloud-demo-ui
    fi

    # Each member's API/UI address, e.g. https://10.107.127.20:8443
    local -a urls
    mapfile -t urls < <(mc lxc query "/1.0/cluster/members?recursion=1" \
        | python3 -c 'import json,sys; [print(m["server_name"], m["url"]) for m in json.load(sys.stdin)]')
    local first="${urls[0]#* }"
    link "MicroCloud UI (cluster)" "${first}/ui/cluster"
    link "Instances" "${first}/ui/project/default/instances"
    link "Ceph storage" "${first}/ui/project/default/storage"
    link "OVN networks" "${first}/ui/project/default/networks"
    local entry
    for entry in "${urls[@]:1}"; do
        link "  same UI via ${entry%% *}" "${entry#* }/ui/"
    done
    echo
    echo -e "  ${DIM}First time in this browser only: import the login certificate${NC}"
    echo -e "    ${BOLD}${UI_PFX}${NC}   (password: ${BOLD}${UI_PFX_PASSWORD}${NC})"
    echo -e "  ${DIM}  Chrome/Chromium: chrome://certificate-manager → Your certificates → Import${NC}"
    echo -e "  ${DIM}  Firefox: Settings → Privacy & Security → View Certificates → Your Certificates → Import${NC}"
    echo -e "  ${DIM}Then open the link, accept the self-signed server certificate warning,${NC}"
    echo -e "  ${DIM}and pick \"MicroCloud demo UI\" when the browser asks for a certificate.${NC}"
}

# --- Landscape ---------------------------------------------------------------
landscape_links() {
    title "Landscape web UI"
    if [[ -n "${LANDSCAPE_FQDN}" ]]; then
        link "Landscape (self-hosted)" "https://${LANDSCAPE_FQDN}/"
    elif [[ -n "${LANDSCAPE_ACCOUNT_NAME}" ]]; then
        link "Landscape SaaS" "https://landscape.canonical.com/"
    else
        warn "Landscape is not configured in demo.conf."
        return 0
    fi
    echo -e "  ${DIM}Go to Instances and filter by the tag microcloud-demo. Accept any Pending ones.${NC}"
}

# --- USG ------------------------------------------------------------------------
# USG is a command-line tool with no web UI of its own: its results are HTML
# reports. The Ubuntu Pro dashboard shows the Pro subscription the instance
# is attached to.
usg_links() {
    title "USG compliance reports and Ubuntu Pro"
    local report found=0
    for report in before after; do
        if [[ -f "${REPORT_DIR}/${report}-report.html" ]]; then
            link "USG report (${report} fix)" "file://${REPORT_DIR}/${report}-report.html"
            found=1
        fi
    done
    (( found )) || echo -e "  ${DIM}USG reports appear here after step 4.${NC}"
    link "Ubuntu Pro dashboard" "https://ubuntu.com/pro/dashboard"
}

case "${what}" in
    microcloud) microcloud_links ;;
    landscape)  landscape_links ;;
    usg)        usg_links ;;
    all)        microcloud_links; landscape_links; usg_links ;;
    *)          die "Usage: $0 [microcloud|landscape|usg|all]" ;;
esac
