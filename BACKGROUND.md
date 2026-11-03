# Background: how this repo works (for beginners)

[`README.md`](README.md) tells you **how to run** the demo. This file explains
**what is going on**: the Canonical products involved, how they fit together,
and what every file in this repo does. It assumes you know basic Linux
(a terminal, `ssh`, `apt`) and nothing about Canonical's ecosystem.

Contents:

1. [The repo in one paragraph](#1-the-repo-in-one-paragraph)
2. [The story the demo tells](#2-the-story-the-demo-tells)
3. [Canonical's ecosystem, explained from zero](#3-canonicals-ecosystem-explained-from-zero)
4. [The hardest part: what runs inside what](#4-the-hardest-part-what-runs-inside-what)
5. [Networking and storage, in plain words](#5-networking-and-storage-in-plain-words)
6. [Step by step: what the scripts really do](#6-step-by-step-what-the-scripts-really-do)
7. [How `demo.py` and the AI co-presenter work](#7-how-demopy-and-the-ai-co-presenter-work)
8. [Tour of every file](#8-tour-of-every-file)
9. [Learn by poking at it yourself](#9-learn-by-poking-at-it-yourself)
10. [Glossary](#10-glossary)
11. [Further reading](#11-further-reading)

---

## 1. The repo in one paragraph

This repo is a **scripted live demo** of Canonical's infrastructure products,
designed to run on a single Ubuntu laptop or server. It creates three virtual
machines, turns them into one private cloud (**MicroCloud**), launches a
workload on that cloud, registers the workload with Canonical's management
tool (**Landscape**), and then measurably hardens its security with the
**Ubuntu Security Guide (USG)**, which comes with **Ubuntu Pro**. A Python
program (`demo.py`) drives the steps, and an AI model (reached through
**OpenRouter**) acts as a "co-presenter" that narrates each step and answers
audience questions. All the real work is done by plain bash scripts in
`scripts/`, which print every command they run so you can learn from them.

## 2. The story the demo tells

The four steps follow the life of a machine in a company:

| Step | Business idea | Product shown | One sentence |
|---|---|---|---|
| 1 | "Turn hardware into a cloud" | MicroCloud (LXD + MicroCeph + MicroOVN) | Three separate machines become one cluster. |
| 2 | "Self-service capacity" | MicroCloud / LXD | You ask the cloud for a server; it picks where to run it. |
| 3 | "Manage from day one" | Ubuntu Pro + Landscape | The new server is attached to Ubuntu Pro, then reports to a central management console. |
| 4 | "Prove you are secure" | Ubuntu Pro + USG (CIS benchmark) | Audit, fix one rule, check again: the pass rate goes up. |

If you know public clouds, a rough mapping is:

| This demo | AWS-ish equivalent |
|---|---|
| MicroCloud cluster | A (tiny) private region you own |
| LXD instance | An EC2 instance |
| MicroCeph | EBS / S3 storage |
| MicroOVN | VPC networking |
| Landscape | Systems Manager (fleet management, patching) |
| USG + CIS | Inspector / Security Hub compliance checks |

## 3. Canonical's ecosystem, explained from zero

**Canonical** is the company behind **Ubuntu**. Around Ubuntu it sells and
maintains tools for running many machines. The ones this repo touches:

### Snaps (how everything is installed)

A **snap** is Canonical's packaging format: a self-contained, versioned app
that updates itself. It is installed with `snap install <name> --channel=<track>/<risk>`,
e.g. `lxd --channel=5.21/stable`. The **channel** pins a version line, which is
why `demo.conf.example` has `LXD_CHANNEL`, `MICROCEPH_CHANNEL`, etc. Snaps
come from the **Snap Store** (`api.snapcraft.io`). The background service that
manages snaps is **snapd**. Snaps auto-refresh (update) by default, which is
why step 1 runs `snap refresh --hold`: nothing should update in the middle of
a demo.

### LXD (compute: VMs and containers)

**LXD** (pronounced "lex-dee") runs two kinds of "instances":

- **Virtual machines** (VMs): a full computer emulated with KVM, its own kernel. Heavier (~1 GiB+ RAM).
- **System containers**: a full Ubuntu userspace sharing the host's kernel.
  Feels like a VM (has systemd, ssh, apt), but costs ~200 MiB. Not the same as
  Docker "app containers"; an LXD container runs a whole OS.

You talk to LXD with the `lxc` command (confusing name, historical reasons):
`lxc launch`, `lxc list`, `lxc exec <instance> -- <command>`,
`lxc file push`. LXD can be **clustered**: several LXD servers act as one,
and a **scheduler** decides which member runs each new instance. LXD also has a
web UI on port 8443.

### MicroCeph (storage)

**Ceph** is a distributed storage system: each machine contributes disks
(each disk is run by a Ceph daemon called an **OSD**), and data is copied
(replicated, 3 copies by default) across machines. Lose one machine, keep
your data. **MicroCeph** is Canonical's easy-to-install snap of Ceph. In
LXD, the Ceph-backed storage pool is called `remote`.

### MicroOVN (networking)

**OVN** (Open Virtual Network) is software-defined networking: it creates
virtual networks that span all machines in the cluster, so an instance gets
the same network whichever member it lands on. It needs an **uplink**: a
real(ish) network it can route out to the internet through. **MicroOVN** is
the easy-to-install snap.

### MicroCloud (the glue)

**MicroCloud** is not a fourth engine; it is an **installer/orchestrator**
that sets up LXD + MicroCeph + MicroOVN on 3 to 50 machines and joins them
into one cluster. You can run it interactively (`microcloud init`) or hand it
a YAML answer file (`microcloud preseed`), which is what this repo does.
After setup you use the cloud through LXD (`lxc` and the LXD web UI); that's
why "the MicroCloud UI" *is* the LXD UI.

### Ubuntu Pro

**Ubuntu Pro** is Canonical's subscription (free for personal use on up to 5
machines). It unlocks extended security updates and compliance tools. A
machine joins with `pro attach <token>`, then services are switched on with
`pro enable <service>`. The token comes from <https://ubuntu.com/pro/dashboard>.

### USG and CIS

The **Ubuntu Security Guide (USG)** is a Pro tool that checks ("audits") a
machine against a security benchmark and can automatically fix ("remediate")
what fails. The **CIS benchmarks** (from the Center for Internet Security) are
widely used checklists of secure settings, e.g. "SSH must not allow root
login". *CIS Level 1 Server* is the baseline profile. Under the hood USG uses
**OpenSCAP**, which writes results as XML (machine-readable) and HTML
(human-readable). A **tailoring file** customises a profile, e.g. "only this
one rule", which is how step 4 fixes (and re-checks) exactly one thing.

### Landscape

**Landscape** is Canonical's web console for managing fleets of Ubuntu
machines: inventory, package updates, running scripts, compliance. Each
managed machine runs a small agent, **landscape-client**, and registers with
`landscape-config`. Landscape exists as **SaaS** (hosted by Canonical at
landscape.canonical.com; you have an *account name*) or **self-hosted** (your
own server; account name is `standalone`). New machines may appear as
**Pending** until an admin clicks **Accept**, and each one uses a **seat**
(licence slot).

### OpenRouter (not Canonical)

**OpenRouter** is a third-party service that gives one API for many AI models
(Claude, GPT, etc.). `demo.py` sends it text and gets narration back. It is
optional: without a key the demo uses fixed, built-in sentences.

### How they relate

```text
 Ubuntu Pro ──── unlocks ────► USG (security audits/fixes)
     │
 Landscape ◄──── reports to ── landscape-client (inside each managed machine)

 MicroCloud = installer that sets up, on every member:
     LXD        (compute: VMs & containers, cluster scheduler, web UI)
     MicroCeph  (storage: replicated disks)
     MicroOVN   (networking: virtual networks across members)
 All of them are delivered as snaps.
```

## 4. The hardest part: what runs inside what

This demo fakes "three physical servers" with three VMs on your laptop. That
means there are **two separate LXDs** and things are nested like Russian dolls.
Getting this picture right makes every script readable.

```text
Your laptop (Ubuntu 24.04)                         ← "host"
│  LXD #1  (installed by ./demo.py setup)            only used to create the 3 VMs
│
├── mc-node-1  (VM) ─┐
├── mc-node-2  (VM)  ├── each runs LXD #2 + MicroCeph + MicroOVN,
├── mc-node-3  (VM) ─┘   and together they form ONE MicroCloud cluster
│                         (mc-node-1 is the "LEADER" the scripts talk through)
│
│        Inside the MicroCloud (LXD #2, clustered):
│        └── mc-instance-1  (container by default, or VM)
│                ├── Ubuntu Pro (attach) → then landscape-client → Landscape (step 3)
│                └── usg (needs Pro)                                    (step 4)
```

So a command has to "hop" through the layers. You'll see this pattern
everywhere in the scripts and in the printed trace:

| Printed as | Actual command | Runs on |
|---|---|---|
| `[host]$ lxc list` | `lxc list` | Your laptop's LXD #1 |
| `[mc-node-1]$ lxc cluster list` | `lxc exec mc-node-1 -- lxc cluster list` | The cluster (LXD #2) |
| `[mc-node-1 → mc-instance-1]$ usg audit …` | `lxc exec mc-node-1 -- lxc exec mc-instance-1 -- usg audit …` | Inside the workload |

In `scripts/common.sh` these hops are wrapped as small functions:

- `mc <cmd>` = run on the leader node (`lxc exec mc-node-1 -- <cmd>`)
- `in_instance <name> <cmd>` = run inside a workload (`mc lxc exec <name> -- <cmd>`)

Why containers for the workload by default? A VM inside a VM needs **nested
virtualization**, which many laptops don't allow, and costs much more RAM.
Step 2 automatically falls back to containers if `/dev/kvm` is missing inside
the nodes.

## 5. Networking and storage, in plain words

### Networks

| Network | Created by | Purpose |
|---|---|---|
| `lxdbr0` (host's default LXD bridge) | `lxd init --auto` in setup | Each node's **first** NIC. Nodes talk to each other here (cluster traffic) and reach the internet. Its subnet becomes `lookup_subnet` in the preseed. |
| `mcbr0` (`10.20.0.0/24` by default) | Step 1 | Each node's **second** NIC, left **without an IP** on purpose. OVN uses it as its **uplink**. No DHCP: OVN takes router addresses from `10.20.0.100–254` itself; `10.20.0.1` is the gateway (NAT to the internet). |
| `default` (OVN network) inside MicroCloud | MicroCloud (step 1) | The network workload instances plug into. Spans all members. |

### Storage

1. On the host, step 1 creates one empty **block volume** per node
   (`mc-node-1-ceph`, 15 GiB) in the host's LXD storage pool.
2. It's attached to the VM as an extra disk. Inside the VM it shows up with
   serial `lxd_ceph`, which is how `node_facts()` finds it (`lsblk -o NAME,SERIAL`).
3. The preseed tells MicroCloud to give that disk to Ceph (`wipe: true`).
4. MicroCeph turns the three disks into one replicated pool, exposed to LXD
   as the storage pool **`remote`**. Instance disks live there.

Step 1 also lowers Ceph's per-disk memory target from 4 GiB to 1 GiB
(`osd_memory_target`) so tiny demo VMs don't run out of RAM.

### "Resource pooling" (a frequent point of confusion)

The pool is the **sum** of the members' CPU/RAM (3 × 2 vCPU / 3 GiB = 6 vCPU /
~9 GiB). It does **not** fuse three machines into one big one: each instance
runs on exactly one member, so one instance can't be bigger than one member.
The pool is the shared budget that *all* instances draw from, and the
scheduler places each new instance where there's room. `scripts/pool_summary.py`
prints exactly this.

## 6. Step by step: what the scripts really do

Every script starts with `source common.sh`, which loads settings
(`demo.conf.example`, then your `demo.conf` on top), defines the helpers, and
turns on `set -euo pipefail` (stop on the first error). Every step is
**idempotent**: run it again and anything already done is skipped or just shown.

### Setup — `scripts/setup_host.sh` (`./demo.py setup`)

Installs snapd (if missing) and the LXD snap on your laptop, adds you to the
`lxd` group (hence "log out and back in"), and runs `lxd init --auto` to create
a default storage pool and the `lxdbr0` bridge.

### Check — `scripts/check_host.sh` (`./demo.py check`)

Read-only checks: `/dev/kvm` exists, LXD usable, enough RAM/CPU/disk,
`NODE_COUNT >= 3`, internet reachable (Snap Store, Ubuntu images, apt archive),
Landscape/Pro settings filled in. `demo.py` then sends a test message to
OpenRouter.

### Step 1 — `scripts/step1_build_microcloud.sh`

1. **Already built?** If `mc-node-1` exists and has a working cluster, just
   show it and exit (this is why you pre-run step 1 before the audience).
2. **Uplink network**: create `mcbr0` (see §5).
3. **VMs**: for each node create a Ceph block volume, `lxc init` an Ubuntu
   24.04 VM with CPU/RAM/disk limits, attach the `mcbr0` NIC and the Ceph disk,
   start it, wait until it boots and snapd has finished first-boot seeding.
4. **Install snaps**: download `snapd`, `core22`, `core24`, `lxd`, `microceph`,
   `microovn`, `microcloud` **once on the host** (`snap_fetch.py`, cached in
   `cache/snaps/`), `lxc file push` them into each VM, and install them
   offline (`snap ack` the signature, then `snap install` the file) in
   parallel, with retries because snapd often restarts itself on first boot.
   Downloading once on the host is far more reliable than three VMs hitting
   the Snap Store at once.
5. **Form the cluster**: inspect each VM (`node_facts`) to find the uplink NIC,
   Ceph disk and subnet; write `logs/microcloud-preseed.yaml`, which looks like:

   ```yaml
   lookup_subnet: 10.x.x.0/24          # where members find each other
   initiator: mc-node-1                # the one that creates the cluster
   session_passphrase: microcloud-demo-…
   systems:
     - name: mc-node-1
       ovn_uplink_interface: enp6s0
       storage: { ceph: [ { path: /dev/sdb, wipe: true } ] }
     # … one entry per node
   ovn:
     ipv4_gateway: 10.20.0.1/24
     ipv4_range: 10.20.0.100-10.20.0.254
     dns_servers: 8.8.8.8
   ```

   Then run `microcloud preseed` on every node at the same time: the joiners
   wait for the initiator, and together they form the cluster.
6. Lower Ceph's memory target, print `lxc cluster list` (all members
   `ONLINE`), and print the web UI links.

### Step 2 — `scripts/step2_provision_instances.sh`

1. Show `lxc cluster list` and the **pool table**: it queries LXD's REST API
   (`lxc query /1.0/resources?target=<member>` per member and
   `/1.0/instances?recursion=1`) and pipes the JSON into `pool_summary.py`.
   Also `lxc storage info remote` for the Ceph pool.
2. `lxc launch ubuntu:24.04 mc-instance-1 -c limits.cpu=1 -c limits.memory=512MiB`
   **without `--target`**, so the cluster scheduler chooses the member.
   (`</dev/null` is there because `lxc launch` otherwise waits for YAML on stdin.)
3. Wait for boot and `cloud-init` to finish, then `lxc list -c ns4tL`: the
   `L` column is **LOCATION**, i.e. which member the cloud picked. Print the
   pool table again to show what was consumed.

### Step 3 — `scripts/step3_register_landscape.sh`

For each instance (in parallel, output to `logs/mc-instance-N-landscape.log`):
`pro attach <PRO_TOKEN>` first (skipped if already attached — the instance
must be on Ubuntu Pro before it registers), `apt-get install landscape-client`,
then

```bash
landscape-config --silent --computer-title MC-Instance_1 \
  --account-name <your account | standalone> --tags microcloud-demo \
  [--registration-key …] [--url https://<fqdn>/message-system --ping-url …] \
  [--ssl-public-key /etc/landscape/server.pem]
```

The bracketed options depend on whether you use SaaS or self-hosted Landscape
(see `demo.conf.example`). On instance `USG_INSTANCE` it then also runs
`pro enable usg`, `apt-get install usg` and `usg generate-tailoring`, so
step 4 has nothing to download. After this, the machine shows up in the Landscape
web UI (maybe as **Pending**, needing **Accept**).

### Step 4 — `scripts/step4_usg_harden.sh`

All inside `mc-instance-<USG_INSTANCE>`:

1. `pro attach <PRO_TOKEN>` (already done in step 3, so normally skipped), `pro enable usg`,
   `apt-get install usg`.
2. **Baseline**: `usg audit cis_level1_server`. Copy the newest results XML
   and HTML report out to `reports/before-*`. `usg_tools.py score` reads the
   XML and prints pass rate / passed / failed.
3. **Choose what to fix**: `usg generate-tailoring` dumps the full profile.
   With `USG_FIX=one` (default), `usg_tools.py pick` chooses `USG_RULE` if it
   is failing, otherwise the first failing rule from a list of *safe* ones
   (rules that won't lock you out, e.g. `sshd_disable_root_login`), and
   `usg_tools.py tailor` selects just that rule. With `USG_FIX=all`,
   `usg_tools.py harden` selects every rule that failed the baseline, minus
   `SKIP_RULES` (firewall default-deny, AIDE, AppArmor enforcement, bootloader,
   mounts, package removals) and anything in `USG_SKIP_RULES`.
4. **Fix**: push the tailoring file back and run
   `usg fix --tailoring-file …`.
5. **Prove it**: with one rule, `usg audit --tailoring-file …` re-checks only
   that rule (seconds instead of a second full audit), and
   `usg_tools.py score before after` lays that result over the baseline for
   the After numbers. With `USG_FIX=all` the full profile is audited again.
   Either way `reports/after-*` is copied out and a Before/After/Change table
   is printed.

(`usg audit` returns a non-zero exit code whenever any rule fails, which is
normal, so those lines end with `|| true`.)

### Other scripts

| Script | Command | What it does |
|---|---|---|
| `ui_links.sh` | `./demo.py ui` | Prints the web UI links. For the LXD UI it creates a browser client certificate once in `ui-access/` (`.pfx`, password `microcloud`) and adds it to the cluster's trust store (`lxc config trust add`). |
| `status.sh` | `./demo.py status` | Shows what exists right now (VMs, cluster, instances). |
| `cleanup.sh` | `./demo.py cleanup` | After you type `delete`: deletes the node VMs (instances die with them), their Ceph volumes and `mcbr0`, and empties `logs/` and `reports/`. Keeps `cache/` and `ui-access/`. Does **not** touch Landscape or your Pro dashboard. |

## 7. How `demo.py` and the AI co-presenter work

`demo.py` uses only the Python standard library (nothing to `pip install`).
It is a thin presenter layer: **it never touches LXD itself**; it runs the
bash scripts and talks to the AI.

```text
 ./demo.py run
     │
     ├─ load_settings()  demo.conf.example → demo.conf → env vars
     │
     └─ for each of STEPS[1..4]:
           banner
           AI intro  ── ask(goal) ──► OpenRouter ──► text   (fallback: step["intro"])
           wait for presenter: Enter / s / q / free-text question → AI answers
           run_script("stepN_….sh")   streams output live, keeps last 150 lines
           success? → AI explains the output (gets the 150-line tail)  (fallback: step["outro"])
           failure? → AI suggests a fix to the presenter
           menu: Enter / r (rerun) / o (open web UIs found in output) / q / question
     │
     └─ AI closing summary, then a Q&A chat loop
```

Key pieces:

- **`STEPS`**: a list of four dicts with `title`, `script`, `goal` (what the AI
  is told), and `intro`/`outro` (fixed narration used when the AI is off or
  unreachable). The wording adapts to `NODE_COUNT` / `INSTANCE_COUNT`.
- **`CoPresenter`**: holds a long **system prompt** with the facts the AI must
  get right (e.g. pooling ≠ one giant computer) and a style ("2–5 short
  sentences, plain text, never invent numbers"). `ask()` sends the system
  prompt, the last 12 messages of history, and the new prompt to OpenRouter's
  chat-completions API via `urllib`. Any error → print a dim notice and use
  the fallback text, so the demo never stops because of the AI.
- **`run_script()`**: runs a script with `stdin=/dev/null` (scripts never need
  keyboard input, and some commands hang if stdin is a pipe), streams output
  to your terminal, and keeps a stripped-of-colour tail for the AI.
- **`open_links()`**: finds `http(s)://` / `file://` links printed by
  `ui_links.sh` (lines formatted as `  <label>  <url>`) and opens them in the
  browser when you press `o`.

Other commands: `step N [--yes]` (one step, `--yes` = no pauses), `ask` (chat
with the AI), `ui`, `check`, and `setup` / `status` / `cleanup`, which simply
run the matching script.

### The command trace (`scripts/common.sh`)

`common.sh` **redefines `lxc` as a bash function** that first prints the
command, then runs the real binary (`command lxc`). It unwraps nested
`lxc exec X -- …` to print the `[host]` / `[mc-node-1]` / `[mc-node-1 → mc-instance-1]`
prefix, replaces secrets (Pro token, Landscape key) with `<hidden>`, and
writes to file descriptor 3 (the terminal) so the trace still shows when the
command's own output goes to a log file. Other helpers:

| Helper | Purpose |
|---|---|
| `show <cmd>` | Print the command in **bold** (a "key" command), then run it. |
| `show_in <inst> <cmd>` | Same, inside an instance. |
| `run_logged <label> <log> <cmd>` | Run a long job, save output to a log **and** show it live prefixed with `label │`. |
| `wait_for <secs> <what> <cmd>` | Retry a command every 3 s until it works or time runs out. |
| `require_lxd` / `require_cluster` | Stop with a helpful message if prerequisites are missing. |
| `info` / `ok` / `warn` / `die` / `title` | Coloured output (`==>`, `✔`, `!`, `✘`). |

## 8. Tour of every file

```text
README.md                  How to install, prepare and present the demo (the operator manual)
BACKGROUND.md              This file
demo.py                    Presenter + AI co-presenter; runs the scripts below
demo.conf.example          Every setting, with comments (defaults). Copy to demo.conf for your keys.
.gitignore                 Keeps demo.conf (secrets) and generated folders out of git
scripts/
  common.sh                Settings loading, command trace, helpers (sourced by every script)
  setup_host.sh            Install LXD on the host
  check_host.sh            Pre-flight checks
  step1_build_microcloud.sh  VMs → MicroCloud cluster
  step2_provision_instances.sh  Pool table + launch instances
  step3_register_landscape.sh   Pro attach, then landscape-client registration
  step4_usg_harden.sh      Pro attach, USG audit → fix one rule → re-check it
  ui_links.sh              Web UI links + browser certificate for the LXD UI
  status.sh / cleanup.sh   Show / delete what the demo created
  snap_fetch.py            Patient, resumable Snap Store downloader (talks to the store API directly,
                           verifies the SHA3-384 hash, writes .snap + .assert like `snap download`)
  pool_summary.py          Turns LXD's resources/instances JSON into the pool table
  usg_tools.py             Reads OpenSCAP XML (score, a rule's result, rules fixed) and builds
                           the tailoring file that selects step 4's focused rule set
Generated at runtime (git-ignored):
  demo.conf                Your keys and overrides
  logs/                    Per-node install/join logs, preseed YAML, Landscape logs
  reports/                 USG before/after XML + HTML reports, tailoring files
  cache/snaps/             Downloaded snaps reused by step 1
  ui-access/               Browser client certificate for the MicroCloud/LXD UI
```

### Settings worth understanding (`demo.conf.example`)

| Setting | Meaning |
|---|---|
| `NODE_COUNT`, `NODE_CPU`, `NODE_MEMORY`, `NODE_ROOT_DISK`, `NODE_CEPH_DISK` | Size of the "servers" (VMs). Minimum 3 nodes: Ceph and the cluster need a majority (quorum). |
| `INSTANCE_COUNT`, `INSTANCE_TYPE`, `INSTANCE_CPU`, `INSTANCE_MEMORY` | The workloads launched in step 2. |
| `UBUNTU_IMAGE` | `ubuntu:24.04` = image from Canonical's image server. |
| `*_CHANNEL` | Which snap version line to install on the nodes. |
| `UPLINK_SUBNET` | First three octets of `mcbr0`. Change if it clashes with your network. |
| `USG_INSTANCE`, `USG_PROFILE`, `USG_FIX`, `USG_SKIP_RULES`, `USG_RULE` | Which instance step 4 hardens, which benchmark, fix one rule (default) or all safe rules, extra rules to skip in all mode, the rule for one-rule mode. |
| `TRACE_COMMANDS` | `no` hides the dimmed background commands. |

## 9. Learn by poking at it yourself

After step 2 has run, these are safe, read-only commands to explore each layer:

```bash
lxc list
```

```bash
lxc exec mc-node-1 -- lxc cluster list
```

```bash
lxc exec mc-node-1 -- lxc list
```

```bash
lxc exec mc-node-1 -- microceph status
```

```bash
lxc exec mc-node-1 -- microovn status
```

```bash
lxc exec mc-node-1 -- lxc storage list
```

```bash
lxc exec mc-node-1 -- lxc network list
```

```bash
lxc exec mc-node-1 -- lxc exec mc-instance-1 -- bash
```

The last one gives you a shell inside the workload; type `exit` to leave.
Inside it you can try `pro status`, `landscape-config --help`, or
`ls /var/lib/usg`.

Other good ways to learn:

- Read `logs/microcloud-preseed.yaml` after step 1: it's the whole cluster
  definition in ~20 lines.
- Run a single script directly, e.g. `bash scripts/step2_provision_instances.sh`,
  and read the printed trace next to the script source.
- Open the LXD web UI (`./demo.py ui`) and find the same members, instances,
  storage and networks you saw in the terminal.
- Ask the AI: `./demo.py ask "what is the difference between LXD and Docker?"`.

## 10. Glossary

| Term | Meaning |
|---|---|
| **Host** | Your physical machine running the demo. |
| **Node / member** | One machine in the MicroCloud cluster (`mc-node-N`, a VM here). |
| **Leader** | In these scripts, `mc-node-1`: the node commands are sent through. (Also the preseed **initiator**.) |
| **Instance** | A VM or container running *on* the cloud (`mc-instance-N`). |
| **System container** | LXD container running a full OS, sharing the kernel. Lighter than a VM. |
| **KVM / `/dev/kvm`** | Linux's hardware virtualization; needed for VMs. |
| **Nested virtualization** | Running VMs inside VMs. Needed only if `INSTANCE_TYPE=vm`. |
| **Snap / channel / snapd** | Canonical package format / version line (e.g. `5.21/stable`) / its daemon. |
| **Assertion (`.assert`)** | The signed metadata proving a `.snap` file is genuine; `snap ack` imports it. |
| **Preseed** | A YAML answer file so MicroCloud sets itself up without questions. |
| **Cluster scheduler** | The LXD logic that picks which member runs a new instance. |
| **Ceph OSD** | The Ceph process that manages one disk. |
| **Replication** | Keeping several copies of data on different members. |
| **OVN uplink** | The network OVN routes through to reach the outside world (`mcbr0` here). |
| **Bridge** | A virtual network switch on a Linux host (`lxdbr0`, `mcbr0`). |
| **NAT** | Lets machines on a private network reach the internet through one address. |
| **cloud-init** | Tool that configures an Ubuntu instance on first boot. |
| **Ubuntu Pro token** | The key that attaches a machine to your Pro subscription. |
| **landscape-client** | The agent inside a machine that reports to Landscape. |
| **Seat** | A licence slot for one machine in Landscape/Pro. |
| **CIS Level 1 Server** | Baseline security checklist for Ubuntu servers. |
| **OpenSCAP / XCCDF** | The scanner USG uses / the XML format of its rules and results. |
| **Tailoring file** | A customised selection of rules from a profile. |
| **Remediation** | Automatically fixing a failed rule (`usg fix`). |
| **Idempotent** | Safe to run again; already-done work is skipped. |
| **OpenRouter** | One API for many AI models; used for the narration. |

## 11. Further reading

- MicroCloud docs: <https://documentation.ubuntu.com/microcloud/>
- LXD docs: <https://documentation.ubuntu.com/lxd/>
- MicroCeph docs: <https://canonical-microceph.readthedocs-hosted.com/>
- MicroOVN docs: <https://canonical-microovn.readthedocs-hosted.com/>
- Snaps: <https://snapcraft.io/docs>
- Ubuntu Pro: <https://ubuntu.com/pro>
- Ubuntu Security Guide: <https://ubuntu.com/security/certifications/docs/usg>
- Landscape docs: <https://ubuntu.com/landscape/docs>
- CIS benchmarks: <https://www.cisecurity.org/cis-benchmarks>
- OpenRouter: <https://openrouter.ai/docs>
