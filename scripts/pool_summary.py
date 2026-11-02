"""Print each cluster member's CPU/RAM, the pooled total, and what instances use.

Input (stdin): one line per member: "<name> <JSON from /1.0/resources>",
optionally followed by "@instances <JSON from /1.0/instances?recursion=1>".

Pooling means the members' capacity is managed and consumed as one total.
Each instance still runs on ONE member, so a single instance can be at most
as large as the largest member; the pool is the sum of what all instances
can use together.
"""
import json
import re
import sys

UNITS = {"": 1, "b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12,
         "kib": 2**10, "mib": 2**20, "gib": 2**30, "tib": 2**40}


def cpu_limit(value, member_cpus):
    """limits.cpu is a count ("2") or a pinned set ("0-1,3"); unset = all CPUs."""
    if not value:
        return None
    if value.isdigit():
        return int(value)
    total = 0
    for part in value.split(","):
        lo, _, hi = part.partition("-")
        total += int(hi or lo) - int(lo) + 1
    return total or member_cpus


def mem_limit(value, member_bytes):
    """limits.memory is "512MiB", "1GB", "50%" ...; unset = no limit."""
    if not value:
        return None
    if value.endswith("%"):
        return member_bytes * float(value[:-1]) / 100
    match = re.fullmatch(r"\s*([\d.]+)\s*([A-Za-z]*)\s*", value)
    if not match or match.group(2).lower() not in UNITS:
        return None
    return float(match.group(1)) * UNITS[match.group(2).lower()]


members, instances = {}, []
for line in sys.stdin:
    if not line.strip():
        continue
    name, payload = line.split(" ", 1)
    if name == "@instances":
        instances = json.loads(payload)
    else:
        res = json.loads(payload)
        members[name] = {"cpu": res["cpu"]["total"], "mem": res["memory"]["total"],
                         "cpu_used": 0, "mem_used": 0.0, "names": []}

unlimited = []
for inst in instances:
    m = members.get(inst.get("location"))
    if m is None:
        continue
    cfg = inst.get("expanded_config", {})
    m["names"].append(inst["name"])
    cpu = cpu_limit(cfg.get("limits.cpu", ""), m["cpu"])
    mem = mem_limit(cfg.get("limits.memory", ""), m["mem"])
    if cpu is None or mem is None:
        unlimited.append(inst["name"])
    m["cpu_used"] += cpu or 0
    m["mem_used"] += mem or 0


def gib(n):
    return f"{n / 2**30:.1f}"


rows = list(members.items())
print()
print(f"  {'Cluster member':<16}{'vCPU':>6}{'RAM (GiB)':>11}   {'Allocated vCPU':>14}{'Allocated RAM':>15}   Instances")
for name, m in rows:
    print(f"  {name:<16}{m['cpu']:>6}{gib(m['mem']):>11}   {m['cpu_used']:>14}{gib(m['mem_used']):>15}   "
          f"{', '.join(m['names']) or '-'}")
print("  " + "-" * 82)
cpu_total = sum(m["cpu"] for _, m in rows)
mem_total = sum(m["mem"] for _, m in rows)
cpu_used = sum(m["cpu_used"] for _, m in rows)
mem_used = sum(m["mem_used"] for _, m in rows)
print(f"  {'POOLED TOTAL':<16}{cpu_total:>6}{gib(mem_total):>11}   {cpu_used:>14}{gib(mem_used):>15}   "
      f"{sum(len(m['names']) for _, m in rows)} instance(s)")
print()
print(f"  Free in the pool: {cpu_total - cpu_used} vCPU and {gib(mem_total - mem_used)} GiB RAM, "
      "for new instances anywhere in the cluster.")
if rows:
    big_cpu = max(m["cpu"] for _, m in rows)
    big_mem = max(m["mem"] for _, m in rows)
    print(f"  One instance runs on one member, so it can be at most {big_cpu} vCPU / {gib(big_mem)} GiB;")
    print("  the pool is what all instances can use together.")
if unlimited:
    print(f"  ({', '.join(unlimited)}: no limit set, shares its member's resources; counted as 0 above.)")
print()
