"""Small helpers that read USG (OpenSCAP) result files for step 4.

  usg_tools.py score  RESULTS.xml [PARTIAL.xml] -> "<pass%> <passed> <failed>"; PARTIAL (a
                                                   re-audit of a few rules) overrides those rules
  usg_tools.py result RESULTS.xml RULE          -> pass / fail / notapplicable / ...
  usg_tools.py pick   RESULTS.xml RULE [RULE..] -> first rule in the list that FAILED
  usg_tools.py tailor IN.xml OUT.xml RULE       -> tailoring file selecting only RULE
  usg_tools.py harden IN.xml OUT.xml RESULTS.xml [SKIP..]
                                                -> tailoring file selecting every rule that
                                                   FAILED in RESULTS.xml, minus SKIP_RULES and
                                                   the extra SKIP patterns; prints the count
  usg_tools.py fixed  BEFORE.xml AFTER.xml [N]  -> "<count>" then up to N rules that went
                                                   fail -> pass, one per line
"""
import fnmatch
import re
import sys
import xml.etree.ElementTree as ET

PREFIX = "xccdf_org.ssgproject.content_rule_"

# Safe, quick-to-fix CIS rules that do not lock you out of the instance.
SAFE_RULES = [
    "sshd_disable_root_login",
    "sshd_set_max_auth_tries",
    "kernel_module_cramfs_disabled",
    "kernel_module_freevxfs_disabled",
    "banner_etc_issue_net",
]

# Rules the opt-in full hardening (USG_FIX=all) never applies, because they would break
# the demo instance or take far too long on stage. Shell-style patterns on the
# short rule name. Everything else that fails the baseline gets fixed.
SKIP_RULES = [
    "aide_*", "package_aide_installed",  # AIDE builds a file database: 10+ minutes
    "*ufw*", "*nftables*", "*iptables*", "*firewall*",  # default-deny would cut Landscape off
    "apparmor_*", "all_apparmor_profiles_*",  # enforcing profiles inside a container
    "grub2_*", "*bootloader*",            # no bootloader in a container
    "partition_for_*", "mount_option_*",  # mounts belong to the host/LXD
    "package_*_removed",                  # don't uninstall packages under the demo
]


def full_id(rule: str) -> str:
    return rule if rule.startswith(PREFIX) else PREFIX + rule


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


def score(path: str, partial: str = "") -> str:
    """Share of checked rules that pass. With PARTIAL, its results replace the
    full audit's for those rules, so one fixed rule needs no second full audit."""
    merged = rule_results(path)
    if partial:
        merged.update({r: v for r, v in rule_results(partial).items()
                       if v in ("pass", "fail")})
    results = list(merged.values())
    passed, failed = results.count("pass"), results.count("fail")
    value = f"{100 * passed / (passed + failed):.1f}" if passed + failed else "?"
    return f"{value} {passed} {failed}"


def pick(path: str, rules: list) -> str:
    results = rule_results(path)
    for rule in [r for r in rules if r] + SAFE_RULES:
        if results.get(full_id(rule)) == "fail":
            return full_id(rule)
    return ""


def tailor(src: str, dst: str, rule: str) -> None:
    rule = full_id(rule)
    text = open(src).read()

    def fix(match: re.Match) -> str:
        tag = match.group(0)
        idref = re.search(r'idref="([^"]+)"', tag)
        selected = "true" if idref and idref.group(1) == rule else "false"
        return re.sub(r'selected="(true|false)"', f'selected="{selected}"', tag)

    text, count = re.subn(r"<(?:\w+:)?select\b[^>]*>", fix, text)
    if rule not in text:
        sys.exit(f"Rule {rule} is not part of this profile")
    open(dst, "w").write(text)


def short(rule: str) -> str:
    return rule[len(PREFIX):] if rule.startswith(PREFIX) else rule


def harden(src: str, dst: str, results_path: str, extra_skip: list) -> int:
    """Select every rule that failed the baseline, except skipped ones."""
    failed = {r for r, v in rule_results(results_path).items() if v == "fail"}
    patterns = SKIP_RULES + [p for p in extra_skip if p]
    chosen = {r for r in failed
              if not any(fnmatch.fnmatch(short(r), pat) for pat in patterns)}

    def fix(match: re.Match) -> str:
        tag = match.group(0)
        idref = re.search(r'idref="([^"]+)"', tag)
        if not idref or not idref.group(1).startswith(PREFIX):
            return tag  # groups and anything else stay as the profile has them
        selected = "true" if idref.group(1) in chosen else "false"
        return re.sub(r'selected="(true|false)"', f'selected="{selected}"', tag)

    text = re.sub(r"<(?:\w+:)?select\b[^>]*>", fix, open(src).read())
    open(dst, "w").write(text)
    return sum(1 for r in chosen if r in text)


def fixed(before: str, after: str, limit: int) -> str:
    b, a = rule_results(before), rule_results(after)
    flipped = sorted(short(r) for r, v in b.items() if v == "fail" and a.get(r) == "pass")
    return "\n".join([str(len(flipped))] + flipped[:limit])


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "score":
        print(score(args[0], args[1] if len(args) > 1 else ""))
    elif cmd == "result":
        print(rule_results(args[0]).get(full_id(args[1]), "unknown"))
    elif cmd == "pick":
        print(pick(args[0], args[1:]))
    elif cmd == "tailor":
        tailor(args[0], args[1], args[2])
    elif cmd == "harden":
        print(harden(args[0], args[1], args[2], args[3:]))
    elif cmd == "fixed":
        print(fixed(args[0], args[1], int(args[2]) if len(args) > 2 else 10))
    else:
        sys.exit(__doc__)
