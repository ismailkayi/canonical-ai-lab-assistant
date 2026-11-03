#!/usr/bin/env bash
# STEP 4 — Audit one instance against CIS with USG, fix a rule, check again.
#   USG_FIX=one  (default) fix only USG_RULE (or the first failing rule from a
#                safe list), then re-check just that rule: fast and light.
#   USG_FIX=all  fix every failing rule except the few in usg_tools.py
#                SKIP_RULES (and USG_SKIP_RULES), then run a second full audit.
source "$(dirname "$0")/common.sh"
require_cluster

inst=$(instance_name "${USG_INSTANCE}")
tools="${REPO_ROOT}/scripts/usg_tools.py"
tailoring=tailoring-demo.xml
mc lxc info "${inst}" >/dev/null 2>&1 || die "${inst} does not exist. Run step 2 first."
SECONDS=0

# Run a USG command and copy the result XML and HTML report it wrote out of
# the instance (only files newer than a marker, so stale runs never count).
usg_run() {
    local label="$1" xml html; shift
    in_instance "${inst}" touch /root/.usg-marker </dev/null
    show_in "${inst}" "$@" </dev/null || true   # non-zero exit = some rules fail, expected
    [[ -n "${label}" ]] || return 0
    xml=$(in_instance "${inst}" sh -c 'find /var/lib/usg -name "*results*.xml" -newer /root/.usg-marker | xargs -r ls -t | head -n1' </dev/null)
    html=$(in_instance "${inst}" sh -c 'find /var/lib/usg -name "*report*.html" -newer /root/.usg-marker | xargs -r ls -t | head -n1' </dev/null)
    [[ -n "${xml}" ]] || die "USG wrote no results — see the output above (out of memory? check USG_RULES)"
    in_instance "${inst}" cat "${xml}" </dev/null >"${REPORT_DIR}/${label}-results.xml"
    [[ -z "${html}" ]] || in_instance "${inst}" cat "${html}" </dev/null >"${REPORT_DIR}/${label}-report.html"
}

# --- 1. USG + the rule set ----------------------------------------------------
title "1/4  Preparing USG on ${inst} (MC-Instance_${USG_INSTANCE})"
if ! in_instance "${inst}" sh -c 'command -v usg' </dev/null >/dev/null; then
    # Normally done in step 3; only here as a fallback.
    if ! in_instance "${inst}" pro status --format json </dev/null | grep -Eq '"attached": *true'; then
        [[ -n "${PRO_TOKEN}" ]] || die "Set PRO_TOKEN in demo.conf"
        show_in "${inst}" pro attach "${PRO_TOKEN}"
    fi
    show_in "${inst}" pro enable usg --assume-yes </dev/null || true
    show_in "${inst}" sh -c 'DEBIAN_FRONTEND=noninteractive apt-get install -yq usg' </dev/null
fi
in_instance "${inst}" test -s /root/tailoring-full.xml </dev/null ||
    in_instance "${inst}" usg generate-tailoring "${USG_PROFILE}" /root/tailoring-full.xml </dev/null >/dev/null
in_instance "${inst}" cat /root/tailoring-full.xml </dev/null >"${REPORT_DIR}/tailoring-full.xml"
read -ra rules <<<"${USG_RULES:-}"
selected=$(python3 "${tools}" select "${REPORT_DIR}/tailoring-full.xml" "${REPORT_DIR}/${tailoring}" "${rules[@]}")
(( selected > 0 )) || die "None of the chosen rules are in ${USG_PROFILE} — check USG_RULES in demo.conf"
in_instance "${inst}" sh -c "cat > /root/${tailoring}" <"${REPORT_DIR}/${tailoring}"
ok "Focused on ${selected} quick CIS rules from ${USG_PROFILE} (SSH, banners, kernel modules, core dumps, password ageing)"

# --- 2. Baseline --------------------------------------------------------------
title "2/4  Baseline audit"
usg_run before usg audit --tailoring-file "/root/${tailoring}"
read -r score_before pass_before fail_before < <(python3 "${tools}" score "${REPORT_DIR}/before-results.xml")
echo
ok "BEFORE:  ${score_before}% of rules pass   (${pass_before} pass, ${fail_before} fail)"

# --- 3. Harden ----------------------------------------------------------------
in_instance "${inst}" usg generate-tailoring "${USG_PROFILE}" /root/tailoring-full.xml </dev/null >/dev/null
in_instance "${inst}" cat /root/tailoring-full.xml </dev/null >"${REPORT_DIR}/tailoring-full.xml"

fix_mode="${USG_FIX:-one}"
if [[ "${fix_mode}" == "one" ]]; then
    rule=$(python3 "${tools}" pick "${REPORT_DIR}/before-results.xml" "${USG_RULE}")
    [[ -n "${rule}" ]] || die "None of the preferred rules are failing — set USG_RULE in demo.conf to a failing rule (see reports/before-report.html)"
    tailoring=tailoring-one-rule.xml
    python3 "${tools}" tailor "${REPORT_DIR}/tailoring-full.xml" "${REPORT_DIR}/${tailoring}" "${rule}"
    title "3/4  Applying ONE CIS rule: ${rule#xccdf_org.ssgproject.content_rule_}"
else
    tailoring=tailoring-harden.xml
    read -ra extra_skip <<<"${USG_SKIP_RULES:-}"
    selected=$(python3 "${tools}" harden "${REPORT_DIR}/tailoring-full.xml" "${REPORT_DIR}/${tailoring}" \
        "${REPORT_DIR}/before-results.xml" "${extra_skip[@]}")
    (( selected > 0 )) || die "No failing rules left to fix — this instance is already hardened (try another USG_INSTANCE)"
    title "3/4  Hardening: fixing ${selected} failing CIS rules in one pass  (takes a few minutes)"
    echo -e "  ${DIM}Skipped on purpose: firewall default-deny, AIDE, AppArmor enforcement, bootloader,"
    echo -e "  mount options and package removals (see SKIP_RULES in scripts/usg_tools.py).${NC}"
fi
in_instance "${inst}" sh -c "cat > /root/${tailoring}" <"${REPORT_DIR}/${tailoring}"
show_in "${inst}" env DEBIAN_FRONTEND=noninteractive usg fix --tailoring-file "/root/${tailoring}" </dev/null || true

# --- 4. Prove it --------------------------------------------------------------
if [[ "${fix_mode}" == "one" ]]; then
    # Only one rule changed, so re-check only that rule (seconds, not minutes) and
    # combine it with the baseline for the After numbers.
    title "4/4  Re-audit: ${rule#xccdf_org.ssgproject.content_rule_}"
    show_in "${inst}" usg audit --tailoring-file "/root/${tailoring}" </dev/null || true
    fetch_latest after
    read -r score_after pass_after fail_after < <(python3 "${tools}" score \
        "${REPORT_DIR}/before-results.xml" "${REPORT_DIR}/after-results.xml")
else
    title "4/4  Re-audit: ${USG_PROFILE}"
    show_in "${inst}" usg audit "${USG_PROFILE}" </dev/null || true
    fetch_latest after
    read -r score_after pass_after fail_after < <(python3 "${tools}" score "${REPORT_DIR}/after-results.xml")
fi

echo
title "Result for ${inst} (MC-Instance_${USG_INSTANCE})"
printf '  %-10s %-8s %-8s %s\n' "" "Pass %" "Pass" "Fail"
printf '  %-10s %-8s %-8s %s\n' "Before" "${score_before}%" "${pass_before}" "${fail_before}"
printf '  %-10s %-8s %-8s %s\n' "After" "${score_after}%" "${pass_after}" "${fail_after}"
printf '  %-10s %-8s %-8s %s\n' "Change" "$(awk -v a="${score_after}" -v b="${score_before}" 'BEGIN{printf "%+.1f", a-b}')" \
    "$(( pass_after - pass_before ))" "$(( fail_after - fail_before ))"
if [[ "${fix_mode}" == "one" ]]; then
    printf '\n  Rule %s:  %s  →  %s\n\n' "${rule#xccdf_org.ssgproject.content_rule_}" \
        "$(python3 "${tools}" result "${REPORT_DIR}/before-results.xml" "${rule}")" \
        "$(python3 "${tools}" result "${REPORT_DIR}/after-results.xml" "${rule}")"
    ok "HTML reports saved: reports/before-report.html (full audit) and reports/after-report.html (that rule)"
else
    mapfile -t flipped < <(python3 "${tools}" fixed "${REPORT_DIR}/before-results.xml" "${REPORT_DIR}/after-results.xml" 12)
    printf '\n  %s rules went fail → pass' "${flipped[0]}"
    (( ${#flipped[@]} > 1 )) && printf ', including:\n' || printf '\n'
    for r in "${flipped[@]:1}"; do printf '    ✔ %s\n' "${r}"; done
    (( flipped[0] > ${#flipped[@]} - 1 )) && printf '    … and %s more (see reports/after-report.html)\n' "$(( flipped[0] - ${#flipped[@]} + 1 ))"
    echo
    ok "HTML reports saved: reports/before-report.html and reports/after-report.html"
fi
bash "${REPO_ROOT}/scripts/ui_links.sh" usg
bash "${REPO_ROOT}/scripts/ui_links.sh" landscape
