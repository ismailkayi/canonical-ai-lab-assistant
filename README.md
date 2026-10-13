# Canonical AI Lab Assistant

Build a working MicroCloud lab on one Ubuntu machine by describing what you
need in plain English.

```text
You:        Create a three-node MicroCloud training lab called demo.
Assistant:  Shows an exact plan: VMs, CPU, memory, disks, and networks.
You:        yes
Assistant:  Creates the VMs, installs MicroCloud, and checks that it is healthy.
```

Install the **`lab-ai` application snap**, then describe your lab in the chat.
The snap includes the Python application, Ansible, OpenTofu, providers, scripts,
playbooks, and Terraform configuration. **LXD and `gemma4` are separate snaps**:
LXD runs the lab VMs, and `gemma4` provides the local AI service.

The assistant shows an exact plan and asks for approval before creating,
expanding, or deleting a lab. Host setup is a separate, explicit action.

> [!IMPORTANT]
> This is a local classic-confinement snap prototype for labs, training, demos,
> and proofs of concept. It is not a production deployment tool or a published
> Snap Store application. Normal development on `main` and `staging` remains
> separate from `prototype/snap-packaging`.

## Contents

**Getting started**

1. [Before you begin](#1-before-you-begin)
2. [Install](#2-install)
3. [Create your first lab](#3-create-your-first-lab)
4. [Use your lab](#4-use-your-lab)
5. [Manage your labs](#5-manage-your-labs)
6. [If something goes wrong](#6-if-something-goes-wrong)

**Reference**

- [How it works](#how-it-works)
- [Choosing the AI model](#choosing-the-ai-model)
- [Lab size and host capacity](#lab-size-and-host-capacity)
- [Network layouts](#network-layouts)
- [Approvals and safety](#approvals-and-safety)
- [Your data and backups](#your-data-and-backups)
- [Updating the assistant](#updating-the-assistant)
- [Configuration](#configuration)
- [Command reference](#command-reference)
- [Detailed troubleshooting](#detailed-troubleshooting)
- [Development](#development)
- [Current limitations](#current-limitations)

---

## 1. Before you begin

You need an **Ubuntu machine** (Ubuntu 24.04 recommended) with:

| Requirement | Why |
|---|---|
| An amd64 machine with snapd and a user with `sudo` access | Installing the local classic snap and preparing the platform requires administrator access |
| Internet access | Platform setup downloads the inference model and lab VM images; application tools and providers are bundled |
| Hardware virtualization | Lab members are virtual machines |
| About 32 GiB RAM and 200 GiB free disk | Enough for the first small lab and the local AI model |

The machine can be a physical server, a desktop, or a VM, including a
public-cloud VM, as long as it can run virtual machines itself.

Check virtualization now:

```bash
ls -l /dev/kvm
```

If this prints `No such file or directory`, the machine cannot run the lab
VMs. Enable virtualization in the BIOS/firmware, or choose a VM or cloud
instance type that supports nested virtualization. See
[LXD cannot create virtual machines](#lxd-cannot-create-virtual-machines).

> [!NOTE]
> Use a machine you are allowed to change. Classic confinement grants broad
> host access. Installing the application does not prepare LXD or select an AI
> model automatically; explicit host setup can install packages, configure LXD,
> and add your user to the `lxd` group.

## 2. Install

Use your normal user account, not a root shell. Git, a Python virtual
environment, pip, Ansible, and OpenTofu do **not** need to be installed separately
to run the application snap.

**Step 1 — Install the application snap**

Obtain the local `lab-ai_0.1.0-prototype.5_amd64.snap` package. In this prototype
workspace it is built under `~/lxdlab/canonical-ai-lab-assistant-snap`:

```bash
cd ~/lxdlab/canonical-ai-lab-assistant-snap
sudo snap install ./lab-ai_0.1.0-prototype.5_amd64.snap --dangerous --classic
snap list lab-ai
snap run lab-ai version
```

If the package was copied elsewhere, install from that location instead.
`--dangerous` permits an unsigned local package; `--classic` acknowledges its
host access. There is no Store install command for this prototype. Building
from source is optional and covered in [Development](#development).

**Step 2 — Check the platform, then prepare it only if needed**

```bash
snap run lab-ai doctor
```

`doctor` checks bundled tools, LXD access, and the inference service without
installing packages or changing host settings. A failed check on a fresh
machine is expected until LXD and `gemma4` are ready.

Ensure your user has an SSH directory even if the platform is already ready;
the assistant reuses or creates `~/.ssh/id_rsa_lab` for lab access:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
```

If LXD and `gemma4` are already prepared, skip host setup. Otherwise, on a
machine you are allowed to configure, explicitly run:

```bash
snap run lab-ai bootstrap --host-setup
```

This can ask for `sudo`, install the separate platform snaps and missing host
prerequisites, initialize LXD, add group membership, and prepare the lab SSH
key. The first model download can take a while. Review the output and do not
use this command to reconfigure an existing staging host unnecessarily.

Plain `snap run lab-ai bootstrap` only prints guidance: it does not authorize
host setup. See [Choosing the AI model](#choosing-the-ai-model) to select a
smaller model before the first inference download.

**Step 3 — Log out and log back in if group membership changed**

End your login session or reconnect over SSH if setup added you to `lxd`.
Then check from a host terminal:

```bash
lxc info
snap run lab-ai doctor
snap run lab-ai check
```

`lxc info` should work without `sudo`. Once inference is ready, the final check
should report:

```text
✓ Inference engine available at ...
```

If it does not, see [The AI service is unavailable](#the-ai-service-is-unavailable).

**Step 4 — Start the assistant**

```bash
snap run lab-ai chat
```

Next time, run the same command from any directory. No repository folder or
Python environment activation is needed. This guide uses `snap run lab-ai` to
select the snap explicitly even if a source-installed command is also on PATH.
Type `help` for help and `quit` to leave. Always use the same user account to
manage the labs whose state is stored under `~/snap/lab-ai/common`.

## 3. Create your first lab

Type this **in the assistant chat**:

```text
Create a three-node MicroCloud training lab called demo.
```

The assistant checks your machine, chooses a suitable size, and shows the
exact plan: the VMs, CPU, memory, disks, and network it will create.

Read the plan, then type:

```text
yes
```

Type `no` instead to cancel. Nothing is created until you type `yes`.

Deployment takes several minutes. When it finishes, the assistant shows:

- the names and IP addresses of the lab members
- whether MicroCloud, LXD, MicroCeph, and MicroOVN are healthy

Use a unique name for each new lab, especially when source/staging labs already
exist on the same LXD host. Snap state is separate, but the host's resource names
and capacity are shared. Do not copy staging Terraform state into the snap or
reuse another lab's prefix.

You can also give exact sizes:

```text
Create a three-node lab called demo with 2 vCPU, 4 GiB RAM, a 30 GiB root
disk, and one 20 GiB Ceph disk per node.
```

## 4. Use your lab

Each lab member is an Ubuntu VM. For the `demo` lab, they are called
`demo-microcloud-node-1`, `demo-microcloud-node-2`, and so on.

In a host terminal (outside the chat), list them and look at the cluster:

```bash
lxc list
lxc exec demo-microcloud-node-1 -- microcloud cluster list
```

To work inside a member, open a shell. Type `exit` to return to the host:

```bash
lxc exec demo-microcloud-node-1 -- bash
```

> [!NOTE]
> Lab members use private IP addresses. If your Ubuntu machine is a remote or
> cloud VM, connect to it with SSH first and use the commands above from there.

Leaving the chat does **not** stop or delete your lab. It keeps using the
machine's resources until you delete it.

## 5. Manage your labs

Type these requests in the chat. A lab called `demo` is managed with the
name `demo_microcloud`.

| To do this | Type |
|---|---|
| See your labs | `List my MicroCloud environments.` |
| Check health | `Check the health of demo_microcloud.` |
| Add a member | `Add one node to demo_microcloud.` |
| Grow to a size | `Scale demo_microcloud to five nodes.` |
| Delete a lab | `Delete demo_microcloud.` |
| Get a size suggestion | `Recommend a three-node lab for this machine.` |
| Ask a question | `Explain how MicroCeph stores data in my lab.` |

Adding, scaling, and deleting always show a plan first and wait for `yes`.
Multiple labs may coexist if each has a unique prefix and the combined active
allocations fit the host budget. To create another minimal lab, for example:

```text
Create another three-node minimal lab called demo-2.
```

You do not need to delete `demo_microcloud` just to create `demo-2_microcloud`.
The snap manages workspaces in its own user state, not in a staging checkout.

> [!WARNING]
> Deleting a lab permanently removes its VMs and disks. Read the plan carefully
> and type `no` if it names the wrong lab.

## 6. If something goes wrong

| Problem | What to do |
|---|---|
| `lxc` asks for `sudo` or permission is denied | Log out and back in, then run `lxc info` |
| The AI service is unavailable | Run `snap run lab-ai doctor` and `snap run lab-ai check` — see [details](#the-ai-service-is-unavailable) |
| The plan does not fit on your machine | Ask for a smaller lab, or delete a lab you no longer need — see [details](#the-plan-does-not-fit) |
| A name is already in use | Choose another lab name |
| Deployment fails | Read the error, then ask `Check the health of demo_microcloud.` — see [details](#a-deployment-fails) |
| The first answer after a break is slow | Normal; the AI model is loading again |
| Python, a virtual environment, or a bundled tool appears missing | Verify the installed snap revision — see [details](#a-bundled-tool-is-missing) |

When an operation fails, no assistant infrastructure operation continues in the
background. LXD resources already created may remain; inspect their state before
retrying. Do not delete `~/snap/lab-ai/common` or purge the application to
"start fresh"; this removes the information needed to manage those labs.

More help: [Detailed troubleshooting](#detailed-troubleshooting).

---

# Reference

The sections below explain the details. You do not need them for normal use.

## How it works

```text
Your request
   → the assistant inspects the machine and existing labs
   → the AI proposes a lab and explains the trade-offs
   → Python checks capacity, names, and safety rules
   → you approve the exact plan
   → OpenTofu creates the LXD VMs, disks, and networks
   → Ansible installs and configures MicroCloud
   → the assistant verifies the finished cluster
```

The AI decides *what* to build and explains why. The snap's Python code does all
the arithmetic, enforces the limits, and runs only the plan you approved. Bundled
OpenTofu and Ansible perform provisioning; providers are available from the
snap's filesystem mirror instead of requiring a runtime download.

Scripts, playbooks, and Terraform assets are read-only under `$SNAP`.
Terraform working files, per-lab state, and inventories are writable under
`$SNAP_USER_COMMON`, normally `~/snap/lab-ai/common`. The LXD daemon, inference
service, and model are not part of the application snap.

Terms used in this guide:

| Term | Meaning |
|---|---|
| Host | The Ubuntu machine where you run the assistant |
| Application snap | `lab-ai`, containing the assistant and automation tools |
| Platform snaps | Separate LXD and `gemma4` services |
| User state | `~/snap/lab-ai/common`, containing the snap-managed lab state |
| Member / node | One VM in your lab |
| Environment / workspace | One complete lab, such as `demo_microcloud` |
| LXD | Runs the lab VMs on the host |
| MicroCloud | Turns the VMs into a cluster with LXD, MicroCeph, and MicroOVN |
| OSD | A Ceph storage disk |

## Choosing the AI model

The assistant uses a local AI model from the `gemma4` inference snap. The
model runs on the host only.

| Model | Download | Use for |
|---|---:|---|
| `e2b` | ~2.9 GB | Small or CPU-only machines, slow connections |
| `e4b` | ~5.0 GB | Default; recommended for most machines |
| `26b` | ~15.8 GB | Not needed for normal lab work |

The model belongs to the separate inference snap, not the application package.
To select `e2b` before first-time host setup, install the inference snap if it is
missing and choose the model explicitly:

```bash
sudo snap install gemma4          # only if gemma4 is not already installed
snap run gemma4 list-models
snap run gemma4 use-model e2b --assume-yes
snap run lab-ai check
```

On an existing installation, omit the install command and use `use-model` to
change the model. It can download a model component and restart the shared
inference service; coordinate with other users before switching it. This does
not resize, replace, or delete any lab VM. Changing `INFERENCE_MODEL` alone
changes the client request; it does not install or select a model in `gemma4`.

## Lab size and host capacity

### How the size is chosen

- If you give exact values, the assistant uses them. If they do not fit, it
  tells you which resource is short; it does not silently shrink them.
- If you leave values out, the assistant chooses them from your purpose and
  the machine's free capacity.
- `small`, `medium`, and `large` describe intent, not fixed sizes.
- Type `sizing` in the chat for a live three-node suggestion, or
  `sizing tiers` for reference sizes.

You can state how much data you plan to store:

```text
Recommend a three-node storage lab with two Ceph disks per member.
I expect about 100 GiB of data.
```

The assistant then sizes Ceph for three copies of the data plus 20% free
space.

### Supported values

| Resource | Supported value |
|---|---|
| Members | 3–50 |
| vCPU per member | 1 or more |
| Memory per member | 1024 MiB or more (automatic sizing uses at least 4 GiB) |
| Root disk | 20 GiB or more |
| Ceph disk | 10 GiB or more |
| Ceph disks per member | 1–8 |
| Local ZFS disk | `0` (off) or 10 GiB or more |

Sizes are in GiB (1 GiB = 1024 MiB).

### Example: what a lab really uses

Three members, each with 8 vCPU, 16 GiB RAM, a 60 GiB root disk, and two
100 GiB Ceph disks:

| Resource | Total |
|---|---:|
| CPU | 24 vCPU |
| RAM | 48 GiB |
| Root disks | 180 GiB |
| Ceph disks (raw) | 600 GiB |
| **Total host disk** | **780 GiB** |
| Usable Ceph space (3 copies) | about 200 GiB |

### Capacity rules

| Resource | Rule |
|---|---|
| CPU | All running LXD instances together may use up to **3 vCPU per host CPU thread**. For example, 32 threads allow 96 vCPU. |
| Memory | The host keeps 20% of its memory (at least 4 GiB) free. If only memory is short, the assistant may offer a limited lab exception, up to 1.25× the host's memory, when enough memory is actually free. |
| Disk | Every disk must fit in the LXD storage pool, keeping 20 GiB free. Disk is never overcommitted. |

When a plan uses more vCPU than the host has threads, or uses the memory
exception, the plan shows an **overcommit warning**. This is allowed for
labs, but VMs share the host, so heavy simultaneous load is slower. Benchmark
results on a shared host are not representative of dedicated hardware.

Additional details:

- Instances in all LXD projects count, including ones not created by this assistant.
- Stopped instances do not count toward CPU and memory, but their disks still use
  space. Check capacity again before starting them alongside a new lab.
- Capacity is measured again right before a plan runs. If it changed, you are
  asked to approve a new plan.
- Extra Ceph disks on the same host are useful for learning Ceph, but do not
  make storage faster. All lab members share one host, so the lab does not
  survive a host failure.

## Network layouts

**Standard (default):** each member has two network interfaces — one for
management, cluster, and storage traffic, and one IP-free uplink for MicroOVN.
This suits most labs.

**Fully segregated (optional):** each member has four interfaces, so you can
teach or demonstrate separated traffic:

| Interface | Address | Traffic |
|---|---|---|
| `mgmt0` | DHCP | Management and MicroCloud |
| `ovn-uplink` | None | External OVN uplink |
| `ovn-underlay` | Static | OVN Geneve tunnels |
| `ceph-general` | Static | Ceph client and replication |

Ask for it in the chat:

```text
Create a three-node network training lab called netlab with fully segregated
four-NIC networking.
```

The assistant picks non-overlapping subnets automatically. The network layout
cannot be changed after deployment; new members reuse it.

## Approvals and safety

Before creating, expanding, or deleting a lab, the assistant:

1. Resolves every value and resource name.
2. Checks capacity, existing state, and name conflicts.
3. Shows the exact plan with a Plan ID.
4. Waits for a standalone `yes` (anything else does not approve it).
5. Checks everything again, then runs only that plan.
6. After creating or expanding a lab, verifies the cluster.

Other protections:

- **Name conflicts:** if any LXD VM, network, profile, or volume with the
  planned name already exists, deployment stops before changing anything.
  The assistant never adopts or deletes resources it did not create.
- **Locking:** snap infrastructure operations for a user are serialized through
  the snap's operation lock. A source checkout uses a different state/lock
  location, so do not run staging and snap mutations concurrently on the same
  host.
- **Recovery:** if the LXD provider creates a network but fails to record it,
  the assistant imports that exact verified network and continues once. Other
  cases stop and report the problem.

Running scripts or OpenTofu commands directly bypasses these approvals. Use
the chat for normal work.

## Your data and backups

The application snap needs its Terraform state to add nodes to, scale, or
delete labs. Use the same host user account for all operations on a lab.

The normal user-state root is **`~/snap/lab-ai/common`** (`$SNAP_USER_COMMON`).
It is separate from the repository and from normal staging state.

| Location | Contents |
|---|---|
| `~/snap/lab-ai/common/terraform/` | Terraform working files, provider initialization, and per-lab state |
| `~/snap/lab-ai/common/inventory_<workspace>.yaml` | Generated Ansible inventory |
| `~/snap/lab-ai/common/deployment_history.json` | Operation history |
| `~/snap/lab-ai/common/` | Documentation cache, runtime files, and provider mirror configuration |
| `~/.ssh/id_rsa_lab` | Private SSH key for the lab VMs |
| `$SNAP/` | Read-only application, scripts, playbooks, Terraform assets, and bundled tooling; not your lab state |

- Back up the **entire common directory**, not just a selected state file, before
  changing machines or updating the prototype. Stop infrastructure operations
  before copying it.
- Back up the lab SSH key securely and keep it private. Git does not back up
  state, inventories, or keys; never commit them.
- Back up important guest data separately. A state backup is not a VM or disk backup.
- Keep using the same user account. Running the assistant with `sudo` or a
  different user can select another state directory and hide your existing labs.
- Do not copy staging state into the snap or delete state to fix a failed deployment.

> [!WARNING]
> Removing or purging the application snap does not destroy its external LXD
> VMs. Delete unwanted labs through the chat first. `snap remove --purge lab-ai`
> discards user state and can leave VMs that the assistant can no longer manage.

See [Updating the assistant](#updating-the-assistant) for a state-preserving update.

## Updating the assistant

Leave the chat and wait for infrastructure operations to finish. Obtain the new
local snap file, back up user state, then install it **over the existing snap**:

```bash
cd ~/lxdlab/canonical-ai-lab-assistant-snap
backup=".snap-test/state-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup" && chmod 700 "$backup"
cp -a "$HOME/snap/lab-ai/common" "$backup/common"
sudo snap install ./lab-ai_0.1.0-prototype.5_amd64.snap --dangerous --classic
snap list lab-ai
snap run lab-ai doctor
snap run lab-ai chat
```

This local-file install replaces the snap revision while preserving
`$SNAP_USER_COMMON`. The update path was tested with an existing lab: Terraform
lineage, serial, VM geometry, and inventory remained unchanged. Installing a new
revision does not resize or redeploy existing labs.

Check `List my MicroCloud environments.` in the chat after updating. Use the
same user and confirm existing labs are visible before creating another one.

**Do not remove the snap or use `--purge` as an update procedure.** Also do not
restore an old backup over a newer live state while operations are running.
The local prototype is not updated from the Snap Store; updating Git files does
not update the installed application. A new build and local snap install are
required. If the package is in a different directory, replace the file path;
your state still belongs to `~/snap/lab-ai/common`. The backup location above is
ignored by Git; keep backups private and copy them to secure backup storage
rather than treating the worktree as their only home.

## Configuration

No configuration is needed for normal use. Override a setting through the
launch environment, then start a new chat. For example:

```bash
INFERENCE_TIMEOUT_SEC=180 snap run lab-ai chat
```

For a custom endpoint, replace the address and model below with the real values:

```bash
INFERENCE_HOST=http://127.0.0.1:8336/v1 \
INFERENCE_MODEL=gemma4-e4b \
INFERENCE_AUTO_DISCOVERY=false \
snap run lab-ai check
```

Exports in a host shell apply to subsequent `snap run lab-ai` commands from that
shell. A working-directory `.env` can also be read, but it is not required and
is not a supported snap configuration directory. Do not edit `$SNAP` files or
assume `snap set lab-ai ...` configures this prototype; no application configure
hook is provided. Leave `$SNAP_USER_COMMON` managed by snapd.

| Variable | Default | Description |
|---|---|---|
| `INFERENCE_HOST` | `http://127.0.0.1:8336` | AI service address (found automatically for the local snap) |
| `INFERENCE_MODEL` | `gemma4` | Model name (resolved automatically) |
| `INFERENCE_ENGINE` | `gemma4` | Inference snap name |
| `INFERENCE_AUTO_DISCOVERY` | `true` | Find the address and model from the snap |
| `INFERENCE_TIMEOUT_SEC` | `120` | Maximum time for one AI request |
| `INFERENCE_MAX_OUTPUT_TOKENS` | `512` | Maximum answer length |
| `INFERENCE_ENABLE_THINKING` | `false` | Extended reasoning; keep `false` for fast answers |
| `INFERENCE_STREAM` | `true` | Show the answer while it is written |
| `INFERENCE_RESTART_TIMEOUT_SEC` | `15` | Wait after the AI service disconnects |
| `INFERENCE_MAX_RETRIES` | `3` | Retries for temporary disconnects |
| `INFERENCE_READY_TIMEOUT_SEC` | `1800` | How long explicit host setup waits for the model |
| `OPERATION_TIMEOUT_SEC` | `3600` | Maximum time for one infrastructure operation |

## Command reference

### Snap administration

| Command | Purpose |
|---|---|
| `sudo snap install ./lab-ai_0.1.0-prototype.5_amd64.snap --dangerous --classic` | Install the local application or update it without purging user state |
| `snap list lab-ai` | Show the installed snap package version and revision |
| `snap info lab-ai` | Show installed package metadata |
| `snap run --shell lab-ai` | Open an advanced shell with the bundled runtime and snap paths |
| `snap services lxd` | Check the separate LXD platform service |
| `snap services gemma4` | Check the separate inference platform service |

### `lab-ai`

The snap's short command is `lab-ai`. This guide uses `snap run lab-ai` to avoid
running a source-installed executable with the same name. Application commands
run as your normal user, not with `sudo`.

| Command | Purpose |
|---|---|
| `snap run lab-ai chat` | Start the assistant from any directory |
| `snap run lab-ai doctor` | Check tools and platform services without privileged host setup |
| `snap run lab-ai check` | Check inference readiness; non-zero exit status on failure |
| `snap run lab-ai bootstrap` | Show guidance only; does not perform host setup |
| `snap run lab-ai bootstrap --host-setup` | Explicitly authorize host preparation; can request `sudo` |
| `snap run lab-ai setup` | Show the configured scripts, AI engine, and setup guidance |
| `snap run lab-ai version` | Show the application version; use `snap list lab-ai` for the prototype package revision |
| `snap run lab-ai --debug check` | Check with debug logging |

The hidden `resolve-sizing` command is an internal bridge used by bundled
scripts; use chat sizing requests for normal operation.

### In the chat

| Input | Purpose |
|---|---|
| `help` | Show help |
| `sizing` | Live three-node size suggestion |
| `sizing tiers` | Reference sizes |
| `yes` / `no` | Approve or cancel the pending plan |
| `quit` | Leave the chat (labs keep running) |

## Detailed troubleshooting

### A bundled tool is missing

The application snap already includes Python, Ansible, `community.general`,
OpenTofu, and its tested providers. A missing Python or package error is not a
request to create a virtual environment or install packages on the host.

First confirm the intended snap revision and command:

```bash
snap list lab-ai
snap run lab-ai version
snap run lab-ai doctor
```

If the installed revision is wrong or incomplete, obtain the corrected local
package and use the state-preserving [update procedure](#updating-the-assistant).
Do not purge user state. LXD and `gemma4` are separate services and are not fixed
by reinstalling Python dependencies.

### LXD asks for `sudo`

If setup added your user to the `lxd` group, log out completely and log back in,
then run `lxc info` and `snap run lab-ai doctor`. If access is still denied,
check `id -nG` and `snap services lxd`; an administrator may need to prepare the
platform. Do not run the application as root to bypass the problem, because it
would use a different lab state directory.

### The AI service is unavailable

The application snap is a client of the separate `gemma4` service. Run:

```bash
snap run lab-ai doctor
snap services gemma4
snap run gemma4 status
snap run lab-ai check
```

If `gemma4` is not installed, prepare the platform explicitly as described in
[Install](#2-install). If its service is stopped, coordinate with other users
before restarting it:

```bash
sudo snap restart gemma4
```

Restarting the shared service interrupts other inference clients. It does not
change lab VMs. To find a custom address or model, inspect status:

```bash
snap run gemma4 status --format json
```

Set `INFERENCE_HOST` and, if necessary, `INFERENCE_MODEL` in the launch
environment using the actual reported values; see [Configuration](#configuration).
No source checkout or Python environment is required for these checks.

### The model download takes a long time

Check progress:

```bash
snap changes
snap run gemma4 status
```

On slow connections, use the smaller model; see
[Choosing the AI model](#choosing-the-ai-model).

### Answers are slow

The model can unload after a period of inactivity, so the first answer after a
break takes longer. To keep it loaded longer (it keeps using memory):

```bash
snap run gemma4 set sleep-idle-seconds=3600
```

This changes the shared inference service and keeps using host memory.
If answers are always slow, use the smaller model, keep
`INFERENCE_ENABLE_THINKING=false`, and reduce competing heavy workloads only
when they are yours to manage. Do not stop existing staging labs to troubleshoot
the snap without checking ownership and capacity first.

### LXD cannot create virtual machines

`/dev/kvm` must exist on the host. On a physical machine, enable
virtualization in the BIOS/firmware. On a VM or cloud instance, enable nested
virtualization or choose an instance type that supports it. A smaller lab does
not fix this.

### The plan does not fit

The error names the resource that is short:

- **CPU:** choose fewer or smaller members.
- **Memory:** use less memory per member, or delete a lab you no longer need.
- **Disk:** use smaller or fewer disks, or free space in the LXD storage pool.
  Stopping a VM does not free its disk space.

Ask the assistant: `Recommend a smaller lab that fits this machine.`
Do not delete resources you do not recognize just to make room.

### Storage capacity cannot be measured

The assistant reads free space from LXD. Check that the pool exists and
reports its usage (replace `default` with your pool name):

```bash
lxc storage list
lxc query /1.0/storage-pools/default/resources
```

### A name is already in use

The error lists the exact conflicts, for example:

```text
network:ca-f21a40ab-up
instance:demo-microcloud-node-1
```

Choose another lab name, or remove only resources you know you own.

### A deployment fails

The assistant shows the error and, when possible, an explanation and a
suggested next step. Then:

1. Ask `List my MicroCloud environments.` and `Check the health of demo_microcloud.`
2. Read the reported state before retrying.
3. To start over with a lab you do not need, delete it through the chat and
   create it again.

Do not delete `~/snap/lab-ai/common/terraform/`, purge the snap, or run cleanup
commands repeatedly. A failed operation may have created resources recorded in
that state. After adding nodes, Ceph can temporarily report recovery activity;
run health again after it settles rather than assuming a partial result is success.

### `Missing Resource State After Create`

For a network the assistant can verify as its own, it recovers automatically.
If recovery is refused, compare the snap's recorded state with LXD. Do not use
another checkout's Terraform directory or a host-installed OpenTofu binary.

Open the packaged shell as the same user who created the lab, then run the
following read-only inspection (replace the workspace and pool names):

```bash
snap run --shell lab-ai
export TF_CLI_CONFIG_FILE="$SNAP_USER_COMMON/tofu.rc"
TF_WORKSPACE=demo_microcloud tofu -chdir="$SNAP_USER_COMMON/terraform" state list
lxc network list
lxc list
lxc storage volume list default
exit
```

The provider mirror configuration is created by `snap run lab-ai` commands.
These inspection commands do not initialize, import, destroy, or delete state.
Do not manually adopt conflicting resources; let the assistant enforce ownership.

### Debug logs

```bash
snap run lab-ai --debug check
snap run lab-ai --debug chat
```

Application diagnostics appear in the terminal. The separate platform services
have their own snapd logs:

```bash
sudo snap logs gemma4 -n=50
sudo snap logs lxd -n=50
```

The application is a CLI, not a daemon; it does not have a `snap services lab-ai`
service to restart. Logs can contain host names, IP addresses, and paths. Review
them before sharing.

## Development

End users run the installed snap; building is only needed when changing the
prototype. Keep all snap edits, builds, and artifacts in the dedicated worktree
on **`prototype/snap-packaging`**. Do not build in or merge back into normal
`main` or `staging` worktrees without explicit approval.

With Snapcraft available, build a local package in that worktree:

```bash
cd ~/lxdlab/canonical-ai-lab-assistant-snap
git branch --show-current        # must be prototype/snap-packaging
snapcraft pack
```

Snapcraft installs build dependencies in its build environment and bundles the
runtime. The end user's host does not need a separate Python development
installation. Source regression tests and checks belong to the development
worktree; see the project test suites and [snap packaging notes](snap/README.md).

Do not commit `.snap`, `parts/`, `prime/`, `stage/`, user state, inventories, or
keys. GitHub contains the prototype source and build definition, not the
locally generated snap file.

For package acceptance, prefer a clean Ubuntu 24.04 VM with nested
virtualization and separate lab prefixes. Verify `doctor`, `check`, `chat`,
standard deployment, two concurrent labs, add-node/scale, health, delete, and
state-preserving local updates. Verify that provider initialization works from
the bundled mirror without a download. Do not perform destructive tests against
existing staging labs.

Project layout:

```text
src/lab_ai_assistant/
├── ai_engine.py       Local AI model, streaming, tools, failure analysis
├── orchestrator.py    Chat loop, approvals, locking, execution
├── planning.py        Plans and deterministic validation
├── sizing.py          Host-aware sizing
├── verification.py    State checks and cluster health
├── doc_fetcher.py     Official documentation lookup
├── tools.py           Tool definitions and validation
├── ui.py              Terminal interface
└── cli.py             Command-line entry point

terraform/main.tf          Read-only Terraform assets copied into snap user state
playbooks/microcloud.yml   Bundled MicroCloud installation playbook
scripts/                   Bundled setup, lifecycle, and diagnostic scripts
snap/snapcraft.yaml        Classic application snap build definition
snap/local/                Packaged launcher and provider mirror setup
snap/python/               Pinned application dependencies for the build
tests/                     Source and snap regression tests
```

## Current limitations

- Labs only; production deployments are out of scope.
- Removing members and scaling down are not automated.
- The network layout cannot be changed after deployment.
- Snap channels are set in `playbooks/microcloud.yml`, not through the chat.
- Custom MicroCloud preseed files are not supported.
- The application snap is a local classic-confinement amd64 prototype, not a Snap Store release.
- All members run on one host, so a lab does not survive a host failure.
- Lab IP addresses are private to the host.

## License

GPL-3.0-or-later
