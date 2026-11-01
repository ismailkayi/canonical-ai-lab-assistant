"""Print a table of each cluster member's CPU/RAM and the pooled total.

Input (stdin): one line per member: "<name> <JSON from /1.0/resources>".
"""
import json
import sys

rows = []
for line in sys.stdin:
    if not line.strip():
        continue
    name, payload = line.split(" ", 1)
    res = json.loads(payload)
    rows.append((name, res["cpu"]["total"], res["memory"]["total"] / 2**30))

print()
print(f"  {'Cluster member':<16}{'vCPU':>6}{'RAM (GiB)':>12}")
for name, cpu, mem in rows:
    print(f"  {name:<16}{cpu:>6}{mem:>12.1f}")
print("  " + "-" * 34)
print(f"  {'POOLED TOTAL':<16}{sum(r[1] for r in rows):>6}{sum(r[2] for r in rows):>12.1f}")
print()
