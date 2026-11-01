"""Small helpers that read USG (OpenSCAP) result files for step 4.

  usg_tools.py score  RESULTS.xml               -> "<score> <passed> <failed>"
  usg_tools.py result RESULTS.xml RULE          -> pass / fail / notapplicable / ...
  usg_tools.py pick   RESULTS.xml RULE [RULE..] -> first rule in the list that FAILED
  usg_tools.py tailor IN.xml OUT.xml RULE       -> tailoring file selecting only RULE
"""
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


def score(path: str) -> str:
    value = "?"
    for el in ET.parse(path).iter():
        if local(el.tag) == "score":
            value = f"{float(el.text):.1f}"
            break
    results = list(rule_results(path).values())
    return f"{value} {results.count('pass')} {results.count('fail')}"


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


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "score":
        print(score(args[0]))
    elif cmd == "result":
        print(rule_results(args[0]).get(full_id(args[1]), "unknown"))
    elif cmd == "pick":
        print(pick(args[0], args[1:]))
    elif cmd == "tailor":
        tailor(args[0], args[1], args[2])
    else:
        sys.exit(__doc__)
