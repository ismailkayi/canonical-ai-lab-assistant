# MicroCloud demo with an AI co-presenter

A scripted, step-by-step live demo that runs on one Ubuntu machine. An AI model
reached through [OpenRouter](https://openrouter.ai) narrates each step and
answers questions from the audience.

> New to MicroCloud, LXD, Landscape or Ubuntu Pro? Read
> [BACKGROUND.md](BACKGROUND.md) first: it explains the products and how
> every part of this repo works.

| Step | What you show | What the audience learns |
|---|---|---|
| **1** | 3 VMs join together into **one MicroCloud** | Separate machines become one cloud |
| **2** | Their CPU, RAM and storage shown as **one pool**, then **one instance** provisioned from it | Resource pooling and on-demand provisioning |
| **3** | The instance registered in **Landscape** as `MC-Instance_1` | Central management from day one |
| **4** | **USG** audits one instance against CIS, fixes **one rule**, and the pass rate goes up | Compliance you can measure |

```text
 Your Ubuntu machine
 ┌───────────────────────────────────────────────────────────────┐
 │  mc-node-1   mc-node-2   mc-node-3            ← Step 1: 3 VMs  │
 │  └──────────────── one MicroCloud ────────────────┘           │
 │        shared compute (LXD) · storage (Ceph) · network (OVN)  │
 │                                                               │
 │    mc-instance-1                     ← Step 2: placed by the   │
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
| RAM | **about 10 GiB free** (on top of whatever else you are running) | 3 VMs × 3 GiB; the instance is a light system container |
| CPU | 8 threads | 3 VMs × 2 vCPU (can be shared) |
| Free disk | 60 GiB | VM disks and Ceph disks |

The defaults are the smallest working MicroCloud: **3 cluster members pooled
into one cloud, and one container instance** from that pool, so the demo runs on a laptop that is busy with other
apps. The narration follows `NODE_COUNT` and `INSTANCE_COUNT` in `demo.conf`; with
more free RAM, use the bigger shapes listed in `demo.conf.example` (up to 10
members and 5 VM instances). The minimum is 3 members. Each extra instance also needs its own Landscape seat.
| Virtualization | `/dev/kvm` exists | The cluster members are real VMs |
| Internet | Yes | Downloads Ubuntu images and packages |

**Three accounts** (all have free options):

| Account | Used in | Where to get it |
|---|---|---|
| OpenRouter API key | The AI co-presenter | <https://openrouter.ai/keys> (a full demo costs a few cents) |
| Landscape | Step 3 | Landscape SaaS account name + registration key, **or** your own Landscape server |
| Ubuntu Pro token | Steps 3 and 4 (attached before Landscape; USG needs Pro) | <https://ubuntu.com/pro/dashboard> (free for up to 5 machines) |

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
| 1. Build MicroCloud | 20–40 min (faster on re-runs: snaps are cached in `cache/`) | **No, prepare it.** Live, the step just shows the finished cluster in seconds. |
| 2. Pool and provision | 5–15 min | Yes. If time is short, prepare this too. |
| 3. Landscape | 3–5 min | Yes |
| 4. USG | 5–10 min (one full CIS audit, one rule fixed and re-checked) | Yes. Talk through the audit while it runs. |

Every step is **safe to run again**: anything that already exists is kept and
shown again.

Step 1 downloads the MicroCloud snaps **once, on this host**, and copies them
into the VMs, instead of letting three fresh VMs pull them from the Snap Store
at the same time (which timed out on busy or VPN connections). The downloads
are kept in `cache/snaps/`, so re-running step 1 after a cleanup is quicker.

Also before you start:

- Open the web UIs in browser tabs (see [Web UIs](#web-uis) below), and log
  in to Landscape.
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

### Every command is shown

Each step prints every command it runs before running it, so nothing happens
out of sight and newcomers can follow along and learn. The prefix says where
the command runs:

```text
[host]$ lxc init ubuntu:24.04 mc-node-1 --vm -c limits.cpu=2 -c limits.memory=3GiB
[mc-node-1]$ snap install microceph --channel=squid/stable
[mc-node-1 → mc-instance-1]$ usg audit cis_level1_server
```

The key commands of each step are **bold**, and background ones (checks,
installs, waits) are dimmed. Secrets such as the Pro token show as `<hidden>`.
To show only the bold ones, set `TRACE_COMMANDS=no` in `demo.conf`.

### What to point at in each step

**Step 1 — One cloud from three machines.**
In `lxc cluster list`, all 3 members show `ONLINE`. *"These could be three
physical servers; MicroCloud turned them into one cloud with a single
definition file."*

**Step 2 — Resource pooling and provisioning.**
The **POOLED TOTAL** row shows all CPU and RAM as one pool. Then the instance
launches without saying where it should run. In the final `lxc list`, the
**LOCATION** column shows the member the cloud picked for it, and the pool
table now shows what the instance **took from the pool** and what is still
**free** for more instances.
*"We ask the pool for capacity; the cloud decides where it runs. Disks sit on
replicated Ceph, so losing one server does not lose data."*

> **What pooling does and does not mean.** The pool is the members' combined
> capacity, managed as one: 3 × 2 vCPU / 3 GiB = 6 vCPU / ~9 GiB that *all
> instances together* can use. It does **not** turn the three VMs into one
> giant computer: each instance runs on exactly one member, so a single
> instance is at most one member's size (here 2 vCPU / ~3 GiB). That is how
> LXD, OpenStack, VMware and public clouds all work. To show the pool being
> consumed, raise `INSTANCE_COUNT` (instances get spread across members), or
> raise `INSTANCE_CPU` / `INSTANCE_MEMORY` up to one member's size.

**Step 3 — Ubuntu Pro + Landscape.**
Each instance is first attached to Ubuntu Pro with `PRO_TOKEN`, then
registered with Landscape. USG is also installed (in the background) on the
instance step 4 hardens, so step 4 itself takes only seconds. The terminal shows a ✔ line and the Landscape link (press **o** to open it).
**Switch to the browser:** Landscape →
Instances, filter by tag `microcloud-demo`. If the machine shows as
**Pending**, select it and click **Accept**. You now see `MC-Instance_1`.
Accepting needs a free seat on your Landscape account (shown as *Remaining
registrations* on the account page).

**Step 4 — USG and CIS.**
USG audits `MC-Instance_1` against **CIS Level 1 Server**, fixes **one** rule
(by default *"disable SSH root login"*), and re-checks just that rule, so there
is no second full audit. The final table shows the **Before** and **After**
pass rate and the rule going `fail → pass`. To remediate every safely fixable
rule instead (much slower and heavier), set `USG_FIX=all`. The HTML reports are
saved in `reports/` (`before-report.html` is the full audit, `after-report.html`
the re-check); press **o** to open them in the browser.

### Web UIs

After steps 1, 2, 3 and 4 the terminal prints the matching Canonical web UIs,
and the prompt offers **`[o] open the web UIs`**. Any time, list (or open) them all:

```bash
./demo.py ui
```

```bash
./demo.py ui --open
```

| UI | Where | Shown after |
|---|---|---|
| **MicroCloud / LXD UI** | `https://<mc-node-1 IP>:8443/ui/` — cluster members, instances, Ceph storage, OVN networks. MicroCloud's web UI *is* the LXD UI. | Steps 1 and 2 |
| **Landscape** | `https://landscape.canonical.com/` (SaaS) or `https://<LANDSCAPE_FQDN>/` | Steps 3 and 4 |
| **USG** | USG has no web UI of its own: its results are the HTML reports in `reports/`, opened as `file://` links | Step 4 |
| **Ubuntu Pro dashboard** | `https://ubuntu.com/pro/dashboard` — the subscription the instances attached to in step 3 | Steps 3 and 4 |

**Logging in to the MicroCloud / LXD UI (once per browser).** The UI logs in
with a client certificate. The demo creates one in `ui-access/` and adds it to
the cluster's trust store for you. Import
`ui-access/microcloud-demo-ui.pfx` (password `microcloud`) into your browser:

- Chrome / Chromium: `chrome://certificate-manager` → *Your certificates* → *Import*
- Firefox: *Settings → Privacy & Security → View Certificates → Your Certificates → Import*

Then open the link, accept the warning about the server's self-signed
certificate, and choose *MicroCloud demo UI* when asked for a certificate.
The members' addresses are on LXD's own bridge, so open the UI on the machine
running the demo.

## 5. Afterwards

```bash
./demo.py cleanup
```

This deletes the cluster VMs (and the instances inside them), their disks, and the
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
| Laptop becomes sluggish or VMs get killed during step 1 | Set `NODE_MEMORY=2GiB` (tight but usually works with containers), run `./demo.py cleanup`, then step 1 again |
| Step 2 says *"using system containers instead"* | The host does not allow nested virtualization. The demo still works; the instances are containers instead of VMs. |
| Step 3: instances never appear in Landscape | Check they are not waiting under **Pending**. For self-hosted Landscape, check `LANDSCAPE_FQDN` and, with a self-signed certificate, set `LANDSCAPE_SSL_CERT`. See `logs/mc-instance-*-landscape.log`. |
| Step 3: `pro attach` fails | The token is wrong or used up. Free tokens cover 5 machines; detach unused ones in the Pro dashboard. |
| Step 4: *"None of the preferred rules are failing"* / *"No failing rules left to fix"* | You already fixed them in a rehearsal. Use another instance (`USG_INSTANCE=2`), set `USG_RULE` to a rule that fails in `reports/before-report.html`, or rebuild the instance with steps 2–3. |
| Step 4 (`USG_FIX=all`): hardening broke something you need | Add the rule to `USG_SKIP_RULES` in `demo.conf` (names are in `reports/after-report.html`), rebuild the instance and run again. |
| AI unavailable / HTTP 401 / unknown model | Check `OPENROUTER_API_KEY`. Pick a model name from <https://openrouter.ai/models> for `OPENROUTER_MODEL`. The demo keeps going with built-in narration. |
| Step 1: `Client.Timeout exceeded`, `connection reset by peer`, `cannot get nonce from store` | This machine's connection to the Snap Store (`api.snapcraft.io`) is unreliable. Step 1 downloads on the host with long timeouts, retries and resume, so a flaky link usually just slows it down; leave it running. If it still fails: disconnect or reconnect the VPN, try another network (e.g. a phone hotspot), and test with `curl -v https://api.snapcraft.io`. If `snap download hello` also fails on this machine, it is the network, not the demo. Already-downloaded snaps in `cache/snaps/` are kept, so each retry continues where it stopped. |
| The MicroCloud UI asks for a certificate or says *not authorized* | Import `ui-access/microcloud-demo-ui.pfx` (password `microcloud`) into the browser, then restart the browser. `./demo.py ui` re-adds the certificate to the cluster if needed. |
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
  step1_build_microcloud.sh      N VMs → one MicroCloud
  step2_provision_instances.sh   Pool summary + instances
  step3_register_landscape.sh    Pro attach + Landscape registration as MC-Instance_<n>
  step4_usg_harden.sh            USG audit → fix one rule → re-check it
  ui_links.sh                    Links to the MicroCloud/LXD, Landscape and USG UIs (+ UI login cert)
  setup_host.sh / check_host.sh / status.sh / cleanup.sh
  snap_fetch.py                  Resumable Snap Store downloader used by step 1
  pool_summary.py, usg_tools.py  Small helpers used by steps 2 and 4
cache/snaps/            Snaps downloaded by step 1 (reused on re-runs; not in git)
ui-access/              Browser login certificate for the MicroCloud UI (not in git)
```

Each step script is plain bash and can also be run on its own, e.g.
`bash scripts/step2_provision_instances.sh`. Read it to see exactly which
commands run.
