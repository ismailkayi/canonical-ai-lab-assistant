#!/usr/bin/env bash
# STEP 3 — Register every instance with Landscape as "MC-Instance_<n>".
source "$(dirname "$0")/common.sh"
# --print: show the commands to type by hand instead of running them.
guide() {
    local account extra="" key_note=""
    if [[ -n "${LANDSCAPE_FQDN}" ]]; then
        account="standalone"
        extra=" --url https://${LANDSCAPE_FQDN}/message-system --ping-url http://${LANDSCAPE_FQDN}/ping"
    else
        account="${LANDSCAPE_ACCOUNT_NAME:-<your-landscape-account>}"
    fi
    [[ -z "${LANDSCAPE_REGISTRATION_KEY}" ]] || extra+=" --registration-key '<your-registration-key>'"
    [[ -z "${LANDSCAPE_SSL_CERT}" ]] || extra+=" --ssl-public-key /etc/landscape/server.pem"
    print_guide <<EOF
# ============================================================================
# STEP 3 by hand: register the instances in Landscape
# Secrets are shown as <placeholders>: type your own values from demo.conf.
# ============================================================================

# Open a shell on the first cluster member; the instances are reached from there.
lxc shell ${LEADER}
EOF
    [[ -z "${LANDSCAPE_SSL_CERT}" ]] || print_guide <<EOF

# Self-signed Landscape certificate: first copy ${LANDSCAPE_SSL_CERT} from the host
# into ${LEADER} (on the host: lxc file push ${LANDSCAPE_SSL_CERT} ${LEADER}/tmp/landscape.pem), then:
for i in \$(seq 1 ${INSTANCE_COUNT}); do
  lxc file push /tmp/landscape.pem ${INSTANCE_PREFIX}-\$i/etc/landscape/server.pem --create-dirs
done
EOF
    print_guide <<EOF

# For each instance: install the Landscape agent, then register it under a friendly name.
for i in \$(seq 1 ${INSTANCE_COUNT}); do
  lxc exec ${INSTANCE_PREFIX}-\$i -- sh -c 'apt-get update -q && apt-get install -yq landscape-client'
  lxc exec ${INSTANCE_PREFIX}-\$i -- landscape-config --silent --computer-title MC-Instance_\$i --account-name ${account} --tags microcloud-demo${extra}
done

exit

# Now open Landscape -> Instances and filter by the tag microcloud-demo.
# If they show as Pending, select them and click Accept.
EOF
}
[[ "${1:-}" != "--print" ]] || { guide; exit 0; }
require_cluster

if [[ -n "${LANDSCAPE_FQDN}" ]]; then
    account="standalone"
    server_args=(--url "https://${LANDSCAPE_FQDN}/message-system" --ping-url "http://${LANDSCAPE_FQDN}/ping")
    console="https://${LANDSCAPE_FQDN}"
elif [[ -n "${LANDSCAPE_ACCOUNT_NAME}" ]]; then
    account="${LANDSCAPE_ACCOUNT_NAME}"
    server_args=()
    console="https://landscape.canonical.com"
else
    die "Set LANDSCAPE_ACCOUNT_NAME (SaaS) or LANDSCAPE_FQDN (self-hosted) in demo.conf"
fi
[[ -z "${LANDSCAPE_REGISTRATION_KEY}" ]] || server_args+=(--registration-key "${LANDSCAPE_REGISTRATION_KEY}")

if [[ -n "${LANDSCAPE_SSL_CERT}" ]]; then
    [[ -f "${LANDSCAPE_SSL_CERT}" ]] || die "LANDSCAPE_SSL_CERT file not found: ${LANDSCAPE_SSL_CERT}"
    lxc file push "${LANDSCAPE_SSL_CERT}" "${LEADER}/tmp/landscape.pem"
fi

register() {
    local i="$1" inst title
    inst=$(instance_name "$i")
    title="MC-Instance_${i}"
    local args=(--silent --computer-title "${title}" --account-name "${account}" --tags microcloud-demo "${server_args[@]}")
    if [[ -n "${LANDSCAPE_SSL_CERT}" ]]; then
        mc lxc file push /tmp/landscape.pem "${inst}/etc/landscape/server.pem" --create-dirs
        args+=(--ssl-public-key /etc/landscape/server.pem)
    fi
    in_instance "${inst}" sh -ec 'export DEBIAN_FRONTEND=noninteractive
        apt-get update -q && apt-get install -yq landscape-client'
    in_instance "${inst}" landscape-config "${args[@]}"
}

title "Registering ${INSTANCE_COUNT} instances with Landscape (account: ${account})"
echo -e "${BOLD}\$ landscape-config --silent --computer-title \"MC-Instance_<n>\" --account-name ${account} --tags microcloud-demo ...${NC}"

pids=()
for i in $(seq 1 "${INSTANCE_COUNT}"); do
    register "$i" >"${LOG_DIR}/$(instance_name "$i")-landscape.log" 2>&1 &
    pids+=("$!")
done
failed=0
for idx in "${!pids[@]}"; do
    i=$((idx + 1))
    if wait "${pids[$idx]}"; then
        ok "$(instance_name "$i")  →  registered as MC-Instance_${i}"
    else
        warn "$(instance_name "$i") failed — see logs/$(instance_name "$i")-landscape.log"
        failed=$((failed + 1))
    fi
done

echo
(( failed == 0 )) || die "${failed} instance(s) failed to register"
ok "All ${INSTANCE_COUNT} instances sent their registration to Landscape."
info "Open ${console} → Instances (filter by tag 'microcloud-demo')."
info "If they show as Pending, select them and click Accept."
