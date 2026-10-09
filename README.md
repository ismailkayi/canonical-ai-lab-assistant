# Canonical AI Lab Assistant

Build a working MicroCloud lab on one Ubuntu machine by describing what you
need in plain English.

```text
You:        Create a three-node MicroCloud training lab called demo.
Assistant:  Shows an exact plan: VMs, CPU, memory, disks, and networks.
You:        yes
Assistant:  Creates the VMs, installs MicroCloud, and checks that it is healthy.
```

You do not need to know how to install MicroCloud, LXD, Ceph, or OVN. The
assistant prepares everything and always asks before it changes anything.

> [!IMPORTANT]
> This tool is for labs, training, demos, and proofs of concept. It is not a
> production deployment tool.

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
| A user with `sudo` access | Setup installs LXD and other tools |
| Internet access | Setup downloads packages, the AI model, and VM images |
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
> Use a machine you are allowed to change. Setup installs system packages and
> configures LXD.

## 2. Install

Run these commands in a terminal on the Ubuntu machine, as your normal user
(not as `root`).

**Step 1 — Install the basic tools and download the assistant**

```bash
sudo apt update
sudo apt install -y git python3 python3-venv curl openssh-client
mkdir -p ~/.ssh && chmod 700 ~/.ssh

git clone https://github.com/ismailkayi/canonical-ai-lab-assistant.git
cd canonical-ai-lab-assistant
```

Keep this folder. The assistant stores the information it needs to manage
your labs here. Run every later command from this folder.

**Step 2 — Prepare the machine**

```bash
./dev.sh --bootstrap
```

This asks for your `sudo` password, then installs LXD, OpenTofu, Ansible, and
the local AI model. The first run downloads several GB and can take a while.
Leave the terminal open until it finishes.

> [!TIP]
> On a slow connection or a small machine, you can install a smaller AI model
> instead. See [Choosing the AI model](#choosing-the-ai-model) **before**
> running this step.

**Step 3 — Log out and log back in**

Setup adds your user to the `lxd` group if needed. Log out and back in (or
reconnect your SSH session) so this takes effect. Then return to the folder and check:

```bash
cd canonical-ai-lab-assistant
lxc info
./dev.sh --check
```

`lxc info` should print LXD details without asking for `sudo`. The check
should report:

```text
✓ Inference engine available at ...
```

If it does not, see [The AI service is unavailable](#the-ai-service-is-unavailable).

**Step 4 — Start the assistant**

```bash
./dev.sh --chat
```

You only need to run setup once. Next time, just run `./dev.sh --chat` from
this folder. Type `help` for help and `quit` to leave.

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

> [!WARNING]
> Deleting a lab permanently removes its VMs and disks. Read the plan carefully
> and type `no` if it names the wrong lab.

## 6. If something goes wrong

| Problem | What to do |
|---|---|
| `lxc` asks for `sudo` or permission is denied | Log out and back in, then run `lxc info` |
| The AI service is unavailable | Run `./dev.sh --diagnose` — see [details](#the-ai-service-is-unavailable) |
| The plan does not fit on your machine | Ask for a smaller lab, or delete a lab you no longer need — see [details](#the-plan-does-not-fit) |
| A name is already in use | Choose another lab name |
| Deployment fails | Read the error, then ask `Check the health of demo_microcloud.` — see [details](#a-deployment-fails) |
| The first answer after a break is slow | Normal; the AI model is loading again |

When an operation fails, nothing keeps running in the background. Do not
delete files in this folder to "start fresh"; they track your existing labs.

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

The AI decides *what* to build and explains why. Python code does all the
arithmetic, enforces the limits, and runs only the plan you approved.

Terms used in this guide:

| Term | Meaning |
|---|---|
| Host | The Ubuntu machine where you run the assistant |
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

To install the smaller `e2b` model, replace **Step 2** of the installation
with:

```bash
./dev.sh
bash scripts/prep_host.sh
bash scripts/install_inference_snap.sh --model e2b
```

Then continue with Step 3. To switch an existing installation to `e2b`, run
the last command on its own.

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
- **Locking:** only one infrastructure operation runs at a time.
- **Recovery:** if the LXD provider creates a network but fails to record it,
  the assistant imports that exact verified network and continues once. Other
  cases stop and report the problem.

Running scripts or OpenTofu commands directly bypasses these approvals. Use
the chat for normal work.

## Your data and backups

The assistant records what it has built in files inside the project folder.
It needs them to add nodes to, scale, or delete your labs.

| Location | Contents |
|---|---|
| `terraform/` | Infrastructure state for each lab |
| `inventory_<workspace>.yaml` | Generated Ansible inventory |
| `~/.canonical-ai-lab-assistant/` | Operation history and documentation cache |
| `~/.ssh/id_rsa_lab` | Private SSH key for the lab VMs |

- Keep the project folder while you have labs. Git does not back these files up.
- Back up `terraform/` before moving the folder.
- Keep the private key private, and never commit these files.
- Back up anything important inside your labs separately.

## Updating the assistant

Leave the chat when no operation is running, then update the same folder:

```bash
git pull --ff-only
./dev.sh --chat
```

If Git reports local changes, review them rather than deleting the folder.
Updating does not change existing labs.

## Configuration

No configuration is needed for normal use. To change a setting, add it to a
`.env` file in the project folder, then start a new chat.

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
| `INFERENCE_READY_TIMEOUT_SEC` | `1800` | How long setup waits for the model |
| `OPERATION_TIMEOUT_SEC` | `3600` | Maximum time for one infrastructure operation |

## Command reference

### `dev.sh`

| Command | Purpose |
|---|---|
| `./dev.sh --bootstrap` | First-time setup of the machine |
| `./dev.sh --chat` | Start the assistant |
| `./dev.sh --check` | Check the AI service |
| `./dev.sh --diagnose` | Run detailed AI service diagnostics |
| `./dev.sh --shell` | Open a shell with the Python environment active |
| `./dev.sh --force-reinstall` | Reinstall the Python package |
| `./dev.sh --clean` | Recreate the Python environment (does not touch labs) |

### `lab-ai`

Available after `source .venv/bin/activate`:

| Command | Purpose |
|---|---|
| `lab-ai chat` | Start the assistant |
| `lab-ai bootstrap` | First-time setup of the machine |
| `lab-ai check` | Check the AI service (non-zero exit status on failure) |
| `lab-ai setup` | Show the configured scripts and AI engine |
| `lab-ai version` | Show the version |
| `lab-ai --debug check` | Check with debug logging |

### In the chat

| Input | Purpose |
|---|---|
| `help` | Show help |
| `sizing` | Live three-node size suggestion |
| `sizing tiers` | Reference sizes |
| `yes` / `no` | Approve or cancel the pending plan |
| `quit` | Leave the chat (labs keep running) |

## Detailed troubleshooting

### `venv` or `ensurepip` is missing

Install `python3-venv` (see [Install](#2-install)), then recreate the Python
environment:

```bash
./dev.sh --clean
```

### LXD asks for `sudo`

Your login session does not yet include the `lxd` group. Log out completely
and log back in, then run `lxc info`.

### The AI service is unavailable

Run the diagnostics:

```bash
./dev.sh --diagnose
```

Useful checks:

```bash
snap services gemma4
gemma4 status
./dev.sh --check
```

If the service is stopped, restart it (this interrupts anyone else using it):

```bash
sudo snap restart gemma4
```

If the service runs on a different address or port, find the `openai`
endpoint and put it in `.env` as `INFERENCE_HOST`:

```bash
gemma4 status --format json
```

### The model download takes a long time

Check progress:

```bash
snap changes
gemma4 status
```

On slow connections, use the smaller model; see
[Choosing the AI model](#choosing-the-ai-model).

### Answers are slow

The model can unload after a period of inactivity, so the first answer after a
break takes longer. To keep it loaded longer (it keeps using memory):

```bash
gemma4 set sleep-idle-seconds=3600
```

If answers are always slow, use the smaller model, keep
`INFERENCE_ENABLE_THINKING=false`, and stop other heavy workloads.

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

Do not delete the `terraform/` folder or run cleanup commands repeatedly.

### `Missing Resource State After Create`

For a network the assistant can verify as its own, it recovers automatically.
If recovery is refused, compare the recorded state with LXD (replace the
workspace and pool names):

```bash
cd terraform
TF_WORKSPACE=demo_microcloud tofu state list
cd ..
lxc network list
lxc list
lxc storage volume list default
```

### Debug logs

```bash
.venv/bin/lab-ai --debug check
```

Logs can contain host names, IP addresses, and paths. Review them before
sharing.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[dev]'

python -m pytest -q
python -m ruff check src tests
python -m black --check src tests
```

Infrastructure checks (require the tools installed by setup):

```bash
for script in scripts/*.sh dev.sh; do bash -n "$script"; done
(cd terraform && tofu init -input=false && tofu fmt -check -recursive && tofu validate)
ansible-playbook --syntax-check -i 'localhost,' playbooks/microcloud.yml
```

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

terraform/main.tf          LXD VMs, networks, and disks
playbooks/microcloud.yml   MicroCloud installation
scripts/                   Setup, lifecycle, and diagnostic scripts
tests/                     Automated tests
```

## Current limitations

- Labs only; production deployments are out of scope.
- Removing members and scaling down are not automated.
- The network layout cannot be changed after deployment.
- Snap channels are set in `playbooks/microcloud.yml`, not through the chat.
- Custom MicroCloud preseed files are not supported.
- Installation is from this source folder; there is no application snap yet.
- All members run on one host, so a lab does not survive a host failure.
- Lab IP addresses are private to the host.

## License

GPL-3.0-or-later
