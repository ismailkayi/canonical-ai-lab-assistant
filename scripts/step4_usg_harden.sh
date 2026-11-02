#!/usr/bin/env bash
# STEP 4 — Audit one instance against CIS with USG, harden it, audit again.
#   USG_FIX=all  (default) fix every failing rule except the few in
#                usg_tools.py SKIP_RULES (and USG_SKIP_RULES): a big score jump.
#   USG_FIX=one  fix only USG_RULE (or the first failing rule from a safe list).
source "$(dirname "$0")/common.sh"
require_cluster

inst=$(instance_name "${USG_INSTANCE}")
tools="${REPO_ROOT}/scripts/usg_tools.py"
mc lxc info "${inst}" >/dev/null 2>&1 || die "${inst} does not exist. Run step 2 first."

# Copy the newest USG result file and HTML report out of the instance.
fetch_latest() {
    local label="$1" xml html
    xml=$(in_instance "${inst}" sh -c 'ls -t /var/lib/usg/*results*.xml | head -n1' </dev/null)
    html=$(in_instance "${inst}" sh -c 'ls -t /var/lib/usg/*report*.html | head -n1' </dev/null || true)
    in_instance "${inst}" cat "${xml}" </dev/null >"${REPORT_DIR}/${label}-results.xml"
    [[ -z "${html}" ]] || in_instance "${inst}" cat "${html}" </dev/null >"${REPORT_DIR}/${label}-report.html"
}

# --- 1. Ubuntu Pro + USG ---------------------------------------------------
title "1/4  Enabling Ubuntu Pro and installing USG on ${inst} (MC-Instance_${USG_INSTANCE})"
pro_status=$(in_instance "${inst}" pro status --format json </dev/null || true)
if grep -Eq '"attached": *true' <<<"${pro_status}"; then
    ok "Already attached to Ubuntu Pro"
else
    [[ -n "${PRO_TOKEN}" ]] || die "Set PRO_TOKEN in demo.conf"   # normally done in step 3
    show_in "${inst}" pro attach "${PRO_TOKEN}" </dev/null
fi
show_in "${inst}" pro enable usg --assume-yes </dev/null || true
show_in "${inst}" sh -c 'DEBIAN_FRONTEND=noninteractive apt-get install -yq usg' </dev/null

# --- 2. Baseline ------------------------------------------------------------
title "2/4  Baseline audit: ${USG_PROFILE}  (takes a few minutes)"
show_in "${inst}" usg audit "${USG_PROFILE}" </dev/null || true   # non-zero exit = some rules fail, expected
fetch_latest before
read -r score_before pass_before fail_before < <(python3 "${tools}" score "${REPORT_DIR}/before-results.xml")
echo
ok "BEFORE:  score ${score_before}%   (${pass_before} rules pass, ${fail_before} fail)"

# --- 3. Harden ----------------------------------------------------------------
in_instance "${inst}" usg generate-tailoring "${USG_PROFILE}" /root/tailoring-full.xml </dev/null >/dev/null
in_instance "${inst}" cat /root/tailoring-full.xml </dev/null >"${REPORT_DIR}/tailoring-full.xml"

fix_mode="${USG_FIX:-all}"
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
title "4/4  Re-audit: ${USG_PROFILE}"
show_in "${inst}" usg audit "${USG_PROFILE}" </dev/null || true
fetch_latest after
read -r score_after pass_after fail_after < <(python3 "${tools}" score "${REPORT_DIR}/after-results.xml")

echo
title "Result for ${inst} (MC-Instance_${USG_INSTANCE})"
printf '  %-10s %-8s %-8s %s\n' "" "Score" "Pass" "Fail"
printf '  %-10s %-8s %-8s %s\n' "Before" "${score_before}%" "${pass_before}" "${fail_before}"
printf '  %-10s %-8s %-8s %s\n' "After" "${score_after}%" "${pass_after}" "${fail_after}"
printf '  %-10s %-8s %-8s %s\n' "Change" "$(awk -v a="${score_after}" -v b="${score_before}" 'BEGIN{d=a-b; printf "%+.1f", d}' 2>/dev/null)" \
    "$(( pass_after - pass_before ))" "$(( fail_after - fail_before ))"
mapfile -t flipped < <(python3 "${tools}" fixed "${REPORT_DIR}/before-results.xml" "${REPORT_DIR}/after-results.xml" 12)
printf '\n  %s rules went fail → pass' "${flipped[0]}"
(( ${#flipped[@]} > 1 )) && printf ', including:\n' || printf '\n'
for r in "${flipped[@]:1}"; do printf '    ✔ %s\n' "${r}"; done
(( flipped[0] > ${#flipped[@]} - 1 )) && printf '    … and %s more (see reports/after-report.html)\n' "$(( flipped[0] - ${#flipped[@]} + 1 ))"
echo
ok "HTML reports saved: reports/before-report.html and reports/after-report.html"
bash "${REPO_ROOT}/scripts/ui_links.sh" usg
bash "${REPO_ROOT}/scripts/ui_links.sh" landscape
