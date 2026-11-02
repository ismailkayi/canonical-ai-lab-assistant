"""Small helpers that read and write USG (OpenSCAP) files for step 4.

  usg_tools.py score  RESULTS.xml               -> "<score> <passed> <failed>"
  usg_tools.py result RESULTS.xml RULE          -> pass / fail / notapplicable / ...
  usg_tools.py select IN.xml OUT.xml [RULE..]   -> tailoring file selecting only these
                                                   rules (DEMO_RULES if none given);
                                                   prints how many were selected
  usg_tools.py fixed  BEFORE.xml AFTER.xml [N]  -> "<count>" then up to N rules that went
                                                   fail -> pass, one per line

Step 4 audits and fixes only a small set of CIS rules instead of the whole
profile: a full CIS audit takes minutes and needs more than 512 MiB of RAM
(oscap gets OOM-killed in the default demo instance). A dozen rules take
seconds and very little memory, and the score is reported for this set.
"""
import re
import sys
import xml.etree.ElementTree as ET

PREFIX = "xccdf_org.ssgproject.content_rule_"

# Quick, low-memory CIS Level 1 rules that usually fail on a fresh Ubuntu
# instance, are fixed by editing a config file (no reboot, no long scans),
# and cannot lock you out (the demo reaches the instance with `lxc exec`).
# Rules this USG version does not have are skipped automatically.
DEMO_RULES = [
    "sshd_disable_root_login",
    "sshd_set_max_auth_tries",
    "sshd_set_max_sessions",
    "sshd_set_maxstartups",
    "sshd_set_login_grace_time",
    "sshd_disable_x11_forwarding",
    "sshd_set_idle_timeout",
    "sshd_set_keepalive",
    "sshd_enable_warning_banner_net",
    "banner_etc_issue",
    "banner_etc_issue_net",
    "kernel_module_cramfs_disabled",
    "kernel_module_freevxfs_disabled",
    "kernel_module_hfs_disabled",
    "kernel_module_hfsplus_disabled",
    "kernel_module_jffs2_disabled",
    "coredump_disable_storage",
    "coredump_disable_backtraces",
    "accounts_maximum_age_login_defs",
    "accounts_password_warn_age_login_defs",
]


def full_id(rule: str) -> str:
    return rule if rule.startswith(PREFIX) else PREFIX + rule


def short(rule: str) -> str:
    return rule[len(PREFIX):] if rule.startswith(PREFIX) else rule


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def rule_results(path: str) -> dict:
    results = {}
    for el in ET.parse(path).iter():
        if local(el.tag) == "rule-result":
            for child in el:
                if local(child.tag) == "result":
                    results[el.get("idref")] = (child.text or "").strip()
    return results


def score(path: str) -> str:
    value = "?"
    for el in ET.parse(path).iter():
        if local(el.tag) == "score":
            value = f"{float(el.text):.1f}"
            break
    results = list(rule_results(path).values())
    return f"{value} {results.count('pass')} {results.count('fail')}"


def select(src: str, dst: str, rules: list) -> int:
    """Write a tailoring file where only `rules` are selected; return how many exist."""
    wanted = {full_id(r) for r in (rules or DEMO_RULES) if r}
    found = set()

    def fix(match: re.Match) -> str:
        tag = match.group(0)
        idref = re.search(r'idref="([^"]+)"', tag)
        if not idref or not idref.group(1).startswith(PREFIX):
            return tag  # groups and anything else stay as the profile has them
        on = idref.group(1) in wanted
        if on:
            found.add(idref.group(1))
        return re.sub(r'selected="(true|false)"', f'selected="{"true" if on else "false"}"', tag)

    text = re.sub(r"<(?:\w+:)?select\b[^>]*>", fix, open(src).read())
    open(dst, "w").write(text)
    return len(found)


def fixed(before: str, after: str, limit: int) -> str:
    b, a = rule_results(before), rule_results(after)
    flipped = sorted(short(r) for r, v in b.items() if v == "fail" and a.get(r) == "pass")
    return "\n".join([str(len(flipped))] + flipped[:limit])


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "score":
        print(score(args[0]))
    elif cmd == "result":
        print(rule_results(args[0]).get(full_id(args[1]), "unknown"))
    elif cmd == "select":
        print(select(args[0], args[1], args[2:]))
    elif cmd == "fixed":
        print(fixed(args[0], args[1], int(args[2]) if len(args) > 2 else 10))
    else:
        sys.exit(__doc__)
