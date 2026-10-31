# MicroCloud demo with an AI co-presenter

A scripted, step-by-step live demo that runs on one Ubuntu machine. An AI model
reached through [OpenRouter](https://openrouter.ai) narrates each step and
answers questions from the audience.

| Step | What you show | What the audience learns |
|---|---|---|
| **1** | 10 VMs join together into **one MicroCloud** | Separate machines become one cloud |
| **2** | Their CPU, RAM and storage shown as **one pool**, then **5 instances** provisioned from it | Resource pooling and on-demand provisioning |
| **3** | The 5 instances registered in **Landscape** as `MC-Instance_1` … `MC-Instance_5` | Central management from day one |
| **4** | **USG** audits one instance against CIS, fixes **one rule**, and the score goes up | Compliance you can measure |

```text
 Your Ubuntu machine
 ┌───────────────────────────────────────────────────────────────┐
 │  mc-node-1   mc-node-2   ...   mc-node-10     ← Step 1: 10 VMs │
 │  └──────────────── one MicroCloud ────────────────┘           │
 │        shared compute (LXD) · storage (Ceph) · network (OVN)  │
 │                                                               │
 │    mc-instance-1 ... mc-instance-5   ← Step 2: placed by the   │
 │        │                               cloud, not by you      │
 │        ├── registered in Landscape   ← Step 3                 │
 │        └── #1 hardened with USG      ← Step 4                 │
 └───────────────────────────────────────────────────────────────┘
```

---

## 1. What you need

**A machine** running Ubuntu 24.04 (a physical server or a VM that supports
nested virtualization):

| | Minimum | Why |
|---|---|---|
| RAM | **48 GiB** (64 GiB is more comfortable) | 10 VMs × 4 GiB, plus the host |
| CPU | 16 threads | 10 VMs × 2 vCPU (can be shared) |
| Free disk | 250 GiB | VM disks and Ceph disks |
| Virtualization | `/dev/kvm` exists | The 10 VMs are real VMs |
| Internet | Yes | Downloads Ubuntu images and packages |

**Three accounts** (all have free options):

| Account | Used in | Where to get it |
|---|---|---|
| OpenRouter API key | The AI co-presenter | <https://openrouter.ai/keys> (a full demo costs a few cents) |
| Landscape | Step 3 | Landscape SaaS account name + registration key, **or** your own Landscape server |
| Ubuntu Pro token | Step 4 (USG needs Pro) | <https://ubuntu.com/pro/dashboard> (free for up to 5 machines) |

> The demo still works without the OpenRouter key: you get fixed built-in
> narration instead of the AI.

## 2. One-time setup (about 10 minutes)

```bash
git clone https://github.com/saif-farid-EMEA/MicroCloud_AI_Assistant.git
```

```bash
cd MicroCloud_AI_Assistant
```

```bash
./demo.py setup
```

`setup` installs LXD. If it says you were added to the `lxd` group, **log out
and back in**, then `cd` back into the folder.

Now add your keys:

```bash
cp demo.conf.example demo.conf
```

```bash
nano demo.conf
```

Fill in `OPENROUTER_API_KEY`, the Landscape lines, and `PRO_TOKEN`. Leave
everything else as it is. Then check everything:

```bash
./demo.py check
```

Fix anything marked ✘ until the check prints **All checks passed** and
**OpenRouter reachable**.

## 3. Before the audience arrives

Building the cluster is the slow part, so **do step 1 in advance**:

```bash
./demo.py step 1 --yes
```

| Step | How long | Run it live? |
|---|---|---|
| 1. Build MicroCloud | 30–60 min | **No, prepare it.** Live, the step just shows the finished cluster in seconds. |
| 2. Pool and provision | 5–15 min | Yes. If time is short, prepare this too. |
| 3. Landscape | 3–5 min | Yes |
| 4. USG | 10–15 min (two CIS audits) | Yes. Talk through the audit while it runs. |

Every step is **safe to run again**: anything that already exists is kept and
shown again.

Also before you start:

- Log in to the Landscape web UI in a browser tab.
- Optional rehearsal of step 4: set `USG_INSTANCE=2` in `demo.conf`, run
  `./demo.py step 4 --yes`, then set it back to `1` for the real demo.

## 4. Presenting

```bash
./demo.py run
```

For each step, the AI introduces it, you press **Enter** to run it, the real
commands and their output scroll by, and then the AI explains what the output
proves.

At every pause you can:

| Type | Result |
|---|---|
| **Enter** | Run the step / go to the next step |
| a question, e.g. `why Ceph?` | The AI answers it out loud on screen |
| `r` | Run the step again |
| `s` | Skip the step |
| `q` | Stop. Resume later with `./demo.py run --from 3` |

After step 4 the AI gives a closing summary, then waits for audience questions.

### What to point at in each step

**Step 1 — One cloud from ten machines.**
In `lxc cluster list`, all 10 members show `ONLINE`. *"These could be ten
physical servers; MicroCloud turned them into one cloud with a single
definition file."*

**Step 2 — Resource pooling and provisioning.**
The **POOLED TOTAL** row shows all CPU and RAM as one pool. Then 5 instances
launch without saying where they should run. In the final `lxc list`, the
**LOCATION** column shows the cloud spread them across different members.
*"We ask the pool for capacity; the cloud decides where it runs. Disks sit on
replicated Ceph, so losing one server does not lose data."*

**Step 3 — Landscape.**
The terminal shows 5 ✔ lines. **Switch to the browser:** Landscape →
Instances, filter by tag `microcloud-demo`. If the machines show as
**Pending**, select them all and click **Accept**. You now see
`MC-Instance_1` … `MC-Instance_5`.

**Step 4 — USG and CIS.**
USG audits `MC-Instance_1` against **CIS Level 1 Server**, fixes **one** rule
(by default *"disable SSH root login"*), and audits again. The final table
shows **Before** and **After** scores and the rule going `fail → pass`. The
full HTML reports are saved in `reports/` (`before-report.html`,
`after-report.html`). Open them in a browser if the audience wants detail.

## 5. Afterwards

```bash
./demo.py cleanup
```

This deletes the 10 VMs (and the instances inside them), their disks, and the
`mcbr0` network. It asks you to type `delete` first. Remove the
`MC-Instance_*` computers in Landscape yourself.

Show what currently exists at any time with:

```bash
./demo.py status
```

## 6. If something goes wrong

When a step fails, the AI reads the error and suggests a fix. Fix it, then
press `r`. Detailed logs are in `logs/`.

| Problem | Fix |
|---|---|
| `./demo.py check` reports not enough memory | In `demo.conf`, lower `NODE_MEMORY` to `3GiB`, or `NODE_COUNT` (minimum 3) |
| Step 2 says *"using system containers instead"* | The host does not allow nested virtualization. The demo still works; the instances are containers instead of VMs. |
| Step 3: instances never appear in Landscape | Check they are not waiting under **Pending**. For self-hosted Landscape, check `LANDSCAPE_FQDN` and, with a self-signed certificate, set `LANDSCAPE_SSL_CERT`. See `logs/mc-instance-*-landscape.log`. |
| Step 4: `pro attach` fails | The token is wrong or used up. Free tokens cover 5 machines; detach unused ones in the Pro dashboard. |
| Step 4: *"None of the preferred rules are failing"* | You already fixed them in a rehearsal. Use another instance (`USG_INSTANCE=3`) or set `USG_RULE` to a rule that fails in `reports/before-report.html`. |
| AI unavailable / HTTP 401 / unknown model | Check `OPENROUTER_API_KEY`. Pick a model name from <https://openrouter.ai/models> for `OPENROUTER_MODEL`. The demo keeps going with built-in narration. |
| Something is stuck | Press `Ctrl+C`, then run the step again. Every step can be re-run. |

## 7. Background you might be asked about

| Term | One-line answer |
|---|---|
| **MicroCloud** | Canonical's small-footprint private cloud: a few commands turn 3–50 machines into a cluster |
| **LXD** | The compute layer. Runs VMs and system containers, and schedules them across the cluster |
| **MicroCeph** | Distributed storage. Every member contributes a disk; data is replicated across members |
| **MicroOVN** | Software-defined networking. Instances get a network that spans the whole cluster |
| **Resource pooling** | Many machines' CPU, RAM and disk presented and consumed as one pool |
| **Provisioning** | Creating instances on demand from that pool, without choosing hardware |
| **Landscape** | Canonical's systems management tool: patching, inventory, compliance and scripts for many Ubuntu machines |
| **Ubuntu Pro** | Canonical's subscription: security patching, compliance tooling (including USG), and more |
| **USG** | Ubuntu Security Guide: audits and fixes Ubuntu against benchmarks such as CIS and DISA-STIG |
| **CIS benchmark** | Industry-standard security configuration guidelines. *Level 1 Server* is the baseline profile |
| **OpenRouter** | A single API in front of many AI models. The AI runs there, not on this machine |

## 8. What is in this repository

```text
demo.py                 Runs the demo and talks to the AI (Python standard library only)
demo.conf.example       All settings with explanations. Copy it to demo.conf.
scripts/
  step1_build_microcloud.sh      10 VMs → one MicroCloud
  step2_provision_instances.sh   Pool summary + 5 instances
  step3_register_landscape.sh    Landscape registration as MC-Instance_<n>
  step4_usg_harden.sh            USG audit → fix one rule → re-audit
  setup_host.sh / check_host.sh / status.sh / cleanup.sh
  pool_summary.py, usg_tools.py  Small helpers used by steps 2 and 4
```

Each step script is plain bash and can also be run on its own, e.g.
`bash scripts/step2_provision_instances.sh`. Read it to see exactly which
commands run.
