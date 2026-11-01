#!/usr/bin/env bash
# STEP 3 — Register every instance with Landscape as "MC-Instance_<n>".
source "$(dirname "$0")/common.sh"
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
