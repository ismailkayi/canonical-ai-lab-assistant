#!/usr/bin/env bash
# STEP 4 — Audit one instance against a focused set of CIS rules with USG, fix
# every failing one in a single pass, audit again. Built to finish in about
# 30 seconds and fit in a 512 MiB instance: a full CIS profile audit takes
# minutes and gets oscap OOM-killed at that size. USG itself is installed in
# step 3, so nothing is downloaded here.
#   USG_RULES  space-separated rules to use (empty = DEMO_RULES in usg_tools.py)
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
ok "BEFORE:  score ${score_before}%   (${pass_before} rules pass, ${fail_before} fail)"
(( fail_before > 0 )) || die "Nothing fails — this instance is already hardened (try another USG_INSTANCE)"

# --- 3. Fix -------------------------------------------------------------------
title "3/4  Fixing all ${fail_before} failing rules in one command"
usg_run "" env DEBIAN_FRONTEND=noninteractive usg fix --tailoring-file "/root/${tailoring}"

# --- 4. Prove it --------------------------------------------------------------
title "4/4  Re-audit"
usg_run after usg audit --tailoring-file "/root/${tailoring}"
read -r score_after pass_after fail_after < <(python3 "${tools}" score "${REPORT_DIR}/after-results.xml")

echo
title "Result for ${inst} (MC-Instance_${USG_INSTANCE}) — ${selected} CIS rules, ${SECONDS}s"
printf '  %-10s %-8s %-8s %s\n' "" "Score" "Pass" "Fail"
printf '  %-10s %-8s %-8s %s\n' "Before" "${score_before}%" "${pass_before}" "${fail_before}"
printf '  %-10s %-8s %-8s %s\n' "After" "${score_after}%" "${pass_after}" "${fail_after}"
printf '  %-10s %-8s %-8s %s\n' "Change" "$(awk -v a="${score_after}" -v b="${score_before}" 'BEGIN{printf "%+.1f", a-b}')" \
    "$(( pass_after - pass_before ))" "$(( fail_after - fail_before ))"
mapfile -t flipped < <(python3 "${tools}" fixed "${REPORT_DIR}/before-results.xml" "${REPORT_DIR}/after-results.xml" 30)
printf '\n  %s rules went fail → pass:\n' "${flipped[0]}"
for r in "${flipped[@]:1}"; do printf '    ✔ %s\n' "${r}"; done
echo
ok "HTML reports saved: reports/before-report.html and reports/after-report.html"
bash "${REPO_ROOT}/scripts/ui_links.sh" usg
bash "${REPO_ROOT}/scripts/ui_links.sh" landscape
