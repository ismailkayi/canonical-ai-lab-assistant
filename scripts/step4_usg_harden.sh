#!/usr/bin/env bash
# STEP 4 — Audit one instance against CIS with USG, fix ONE rule, audit again.
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
    [[ -n "${PRO_TOKEN}" ]] || die "Set PRO_TOKEN in demo.conf"
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

rule=$(python3 "${tools}" pick "${REPORT_DIR}/before-results.xml" "${USG_RULE}")
[[ -n "${rule}" ]] || die "None of the preferred rules are failing — set USG_RULE in demo.conf to a failing rule (see reports/before-report.html)"
short_rule="${rule#xccdf_org.ssgproject.content_rule_}"

# --- 3. Fix exactly one rule ------------------------------------------------
title "3/4  Applying ONE CIS rule: ${short_rule}"
in_instance "${inst}" usg generate-tailoring "${USG_PROFILE}" /root/tailoring-full.xml </dev/null >/dev/null
in_instance "${inst}" cat /root/tailoring-full.xml </dev/null >"${REPORT_DIR}/tailoring-full.xml"
python3 "${tools}" tailor "${REPORT_DIR}/tailoring-full.xml" "${REPORT_DIR}/tailoring-one-rule.xml" "${rule}"
in_instance "${inst}" sh -c 'cat > /root/tailoring-one-rule.xml' <"${REPORT_DIR}/tailoring-one-rule.xml"
show_in "${inst}" usg fix --tailoring-file /root/tailoring-one-rule.xml </dev/null || true

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
printf '\n  Rule %s:  %s  →  %s\n\n' "${short_rule}" \
    "$(python3 "${tools}" result "${REPORT_DIR}/before-results.xml" "${rule}")" \
    "$(python3 "${tools}" result "${REPORT_DIR}/after-results.xml" "${rule}")"
ok "HTML reports saved: reports/before-report.html and reports/after-report.html"
