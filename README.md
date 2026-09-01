# Canonical AI Lab Assistant

Canonical AI Lab Assistant turns a natural-language request into a working
MicroCloud lab on your Ubuntu machine.

Tell the assistant what you want to learn, demonstrate, or test. It inspects the
host, proposes a topology, shows the exact plan for approval, provisions LXD
virtual machines with OpenTofu, configures MicroCloud with Ansible, and verifies
the finished cluster.

> [!IMPORTANT]
> This project creates local lab, training, demo, and proof-of-concept
> environments. It is not a production deployment tool.

## Contents

- [What you can do](#what-you-can-do)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Create your first lab](#create-your-first-lab)
- [Common tasks](#common-tasks)
- [Understand your deployment](#understand-your-deployment)
- [Safety model](#safety-model)
- [Configuration](#configuration)
- [Command reference](#command-reference)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Current limitations](#current-limitations)

## What you can do

- Design a MicroCloud topology from a plain-English request.
- Size the lab against live CPU, memory, and storage capacity.
- Create 3-50 MicroCloud members as LXD virtual machines.
- Configure LXD, MicroCeph, and MicroOVN automatically.
- Attach 1-8 virtual Ceph OSD disks to each member.
- Add an optional local ZFS disk to each member.
- Choose a simple two-NIC network or a fully segregated four-NIC layout.
- List, expand, verify, and delete environments through chat.
- Ask questions grounded in current official Canonical documentation.
- Diagnose deployment failures with deterministic evidence and AI-assisted
  explanations.

## How it works

```text
Natural-language request
        |
        v
Observe the host and existing labs
        |
        v
AI proposes a topology and selects an action
        |
        v
Python validates names, capacity, state, and safety
        |
        v
User approves the exact immutable plan
        |
        v
OpenTofu creates LXD infrastructure
        |
        v
Ansible configures MicroCloud, MicroCeph, and MicroOVN
        |
        v
Independent post-deployment verification
```

The AI plans, explains, and recommends trade-offs. Deterministic code owns live
facts, hard limits, resource names, approval binding, locking, and
postconditions. Infrastructure changes happen only after approval.

## Requirements

Use an Ubuntu host with:

- Ubuntu 24.04 recommended
- Python 3.10 or newer
- A user account with `sudo` access
- Hardware virtualization available to LXD
- Internet access for snaps, Ubuntu images, and documentation
- Enough CPU, RAM, and storage for the requested nested VMs

The bootstrap process installs or prepares:

- snapd
- LXD
- OpenTofu
- Ansible and the required collection
- A dedicated lab SSH key
- A local Canonical inference snap (`gemma4` by default)

### Running inside a VM

The assistant creates LXD **virtual machines**, so a host VM must expose nested
hardware virtualization. If nested virtualization is unavailable, LXD VM
creation will fail even if LXD itself installs successfully.

### Model download

The inference snap is small, but its selected model is downloaded separately.
The first bootstrap can therefore take several minutes, depending on your
connection.

Approximate `gemma4` model sizes:

| Model | Approximate download | Recommended use |
|---|---:|---|
| `e2b` | 2.9 GB | Small, CPU-only, or bandwidth-constrained hosts |
| `e4b` | 5.0 GB | Default; balanced local experience |
| `26b` | 15.8 GB | Large-memory hosts only |

The configured value `gemma4` is a generic alias. Auto-discovery resolves it to
the model currently selected by the snap; a fresh default installation normally
selects `e4b`.

After cloning the repository, run this from its root to select the smaller model
explicitly:

```bash
bash scripts/install_inference_snap.sh --model e2b
```

## Quick start

### 1. Clone the repository

```bash
git clone https://github.com/ismailkayi/canonical-ai-lab-assistant.git
cd canonical-ai-lab-assistant
```

### 2. Bootstrap the host

```bash
./dev.sh --bootstrap
```

`dev.sh` creates the Python virtual environment, installs the current checkout,
and runs the host bootstrap. It prompts for your `sudo` password before changing
host-level packages and services.

If bootstrap adds your user to the `lxd` group, log out and back in before
continuing. This is required for the new group membership to take effect.

### 3. Check local inference

```bash
./dev.sh --check
```

Expected result:

```text
✓ Inference engine available at http://127.0.0.1:8336
  Model: gemma4
```

### 4. Start the assistant

```bash
./dev.sh --chat
```

Use `quit` to leave the chat and `help` to display the built-in help.

## Create your first lab

Start with a small three-node environment:

```text
Create a three-node MicroCloud training lab called demo.
Use 2 vCPU, 4 GB RAM, a 30 GB root disk, and one 20 GB Ceph disk per node.
```

The assistant will:

1. Inspect the live host and existing environments.
2. Resolve all omitted values.
3. Check capacity and LXD resource-name collisions.
4. Display an exact plan and Plan ID.
5. Wait for your approval.

Review the plan, then reply:

```text
yes
```

The command remains attached to the terminal while OpenTofu and Ansible run.
When it finishes, the assistant reports:

- member names and management IP addresses
- LXD UI URLs
- MicroCloud, LXD, MicroCeph, and MicroOVN membership
- Ceph health and OSD count
- network-plane health when four-NIC networking is enabled

### Let the assistant choose the size

You do not need to provide every number:

```text
Create a lightweight three-node MicroCloud lab for a short training session.
Call it training.
```

The assistant uses live host capacity and your stated purpose to propose a
suitable size.

## Common tasks

Run these requests inside `./dev.sh --chat`.

### List environments

```text
List my MicroCloud environments.
```

### Check cluster health

```text
Check the health of demo_microcloud.
```

### Add members

```text
Add one node to demo_microcloud.
```

New members inherit the saved image, CPU, memory, disks, storage pool, network
mode, and dedicated network CIDRs.

### Scale to a larger total

```text
Scale demo_microcloud to five nodes.
```

Scale-up uses the same safe live member-addition workflow. Downscale is not
automated.

### Delete an environment

```text
Delete demo_microcloud.
```

Deletion displays an approval-bound destroy plan before removing the
environment.

### Ask a documentation-backed question

```text
Using the current official documentation, explain the recommended MicroCloud
member count for a training environment.
```

## Understand your deployment

### Environment names

A deployment prefix such as `demo` produces:

```text
Terraform workspace: demo_microcloud
LXD member names:     demo-microcloud-node-1, ...
```

Use the workspace name for list, health, scale, add, and delete requests.

### Resource sizing

| Resource | Supported value |
|---|---|
| Members | 3-50 |
| vCPU per member | 1 or more |
| Memory per member | 1024 MiB or more |
| Root disk | 20 GiB or more |
| Ceph OSD disk | 10 GiB or more |
| Ceph OSD disks per member | 1-8 |
| Local ZFS disk | `0` to disable; otherwise 10 GiB or more |
| Image | Ubuntu 24.04 by default |

Ceph and optional local disks are LXD block volumes in the selected host storage
pool.

### Network layouts

#### Standard two-NIC layout

This is the default and is appropriate for most labs:

| Interface | Purpose |
|---|---|
| Management NIC | MicroCloud lookup, management, and Ceph traffic |
| IP-free OVN uplink | External connectivity for MicroOVN |

Example:

```text
Create a small three-node MicroCloud lab called demo.
```

#### Fully segregated four-NIC layout

Use this mode to teach or demonstrate separated traffic planes:

| Interface | Addressing | Purpose |
|---|---|---|
| `mgmt0` | DHCP | Management and MicroCloud lookup |
| `ovn-uplink` | No IP address | External OVN uplink |
| `ovn-underlay` | Static | OVN Geneve encapsulation |
| `ceph-general` | Static | Ceph public/client and internal/replication traffic |

Example:

```text
Create a three-node network training lab called network-demo.
Use fully segregated four-NIC networking with dedicated OVN underlay and Ceph
planes.
```

The assistant selects non-overlapping `/24` subnets unless you explicitly
provide advanced `ovn_underlay_cidr` and `ceph_network_cidr` values. The mode and
CIDRs become immutable deployment geometry and are reused during expansion.

### Host-aware capacity and lab overcommit

The normal policy is conservative and uses allocated VM limits, live host
memory, a host reserve, and available storage.

Overcommit is never offered as an initial option. If a fresh lab fails **only**
because of CPU or RAM allocation, the assistant may evaluate a bounded fallback
for a short-lived lab, demo, or training workload:

- allocated vCPU must remain at or below 1.50x physical CPU
- allocated RAM must remain at or below 1.25x physical RAM
- every LXD instance in every project must have readable CPU and RAM limits
- live `MemAvailable` must retain a deterministic minimum
- storage is never overcommitted

If the AI recommends the fallback, the approval panel shows the exact current
allocation, after-plan allocation, ratios, and risks. Reply `yes` only after
reviewing that warning. Capacity is measured again immediately before
execution.

### LXD name-collision protection

Terraform workspaces do not provide separate LXD namespaces. Before approval and
again before execution, the assistant checks every profile, network, instance,
and custom-volume name that the plan will create.

If a managed, unmanaged, or orphaned LXD resource already uses one of those
names, deployment stops before making changes and reports the exact conflicts.
Choose another environment prefix or remove only resources you know you own.

Short network names use a persisted eight-character hash, for example:

```text
ca-f21a40ab-up
ca-f21a40ab-ov
ca-f21a40ab-ce
```

### Existing LXD resources

The assistant does not automatically adopt or delete unrelated LXD resources.
Existing networks, profiles, instances, and volumes can remain on the host as
long as their names do not conflict with the exact deployment manifest.

## Safety model

Every infrastructure-changing action follows the same workflow:

1. Resolve all parameters and names.
2. Observe live host and Terraform state.
3. Validate schema, capacity, topology, storage, and collisions.
4. Display the exact immutable plan and Plan ID.
5. Require a standalone confirmation.
6. Acquire a shared infrastructure lock.
7. Revalidate state and capacity.
8. Execute the approved action.
9. Verify deterministic postconditions.

If the environment changes between planning and confirmation, execution is
blocked and a new plan is required.

### Provider state recovery

Some versions of the LXD Terraform provider can create a network but fail to
record it in Terraform state. Recovery is limited to networks and is
fail-closed:

1. Verify the exact expected network name.
2. Verify workspace ownership, role, CIDR, bridge type, and IP-free shape.
3. Import only that verified network into Terraform state.
4. Continue the same approved apply once.

Foreign resources, metadata mismatches, and missing-state VM, profile, or volume
errors are never recovered automatically.

## Configuration

Configuration can be placed in a `.env` file in the repository root or exported
in the shell.

| Variable | Default | Description |
|---|---|---|
| `INFERENCE_ENGINE` | `gemma4` | Local inference snap command |
| `INFERENCE_AUTO_DISCOVERY` | `true` | Discover endpoint and model from snap status |
| `INFERENCE_HOST` | `http://127.0.0.1:8336` | OpenAI-compatible service root or API base |
| `INFERENCE_MODEL` | `gemma4` | Model name; generic names can resolve automatically |
| `INFERENCE_TIMEOUT_SEC` | `120` | Maximum time for one inference request |
| `INFERENCE_MAX_OUTPUT_TOKENS` | `512` | Output-token limit per response |
| `INFERENCE_ENABLE_THINKING` | `false` | Enable extended hidden reasoning |
| `INFERENCE_STREAM` | `true` | Show the response while it is generated |
| `INFERENCE_RESTART_TIMEOUT_SEC` | `15` | Wait time after a local inference disconnect |
| `INFERENCE_MAX_RETRIES` | `3` | Retry count for transient disconnects |
| `OPERATION_TIMEOUT_SEC` | `3600` | Timeout for infrastructure operations |
| `LOG_LEVEL` | `INFO` | Application logging level |

`INFERENCE_ENGINE` and `INFERENCE_AUTO_DISCOVERY` are advanced overrides and do
not need to be added to `.env` for the normal local setup.

Example `.env`:

```env
INFERENCE_HOST=http://127.0.0.1:8336
INFERENCE_MODEL=gemma4
INFERENCE_TIMEOUT_SEC=120
INFERENCE_MAX_OUTPUT_TOKENS=512
INFERENCE_ENABLE_THINKING=false
INFERENCE_STREAM=true
INFERENCE_RESTART_TIMEOUT_SEC=15
INFERENCE_MAX_RETRIES=3
OPERATION_TIMEOUT_SEC=3600
LOG_LEVEL=INFO
```

Keep `INFERENCE_ENABLE_THINKING=false` for the normal local experience.
Thinking-capable models can otherwise consume the entire response budget before
producing a visible answer.

## Command reference

### Recommended launcher

| Command | Purpose |
|---|---|
| `./dev.sh --bootstrap` | Create/update `.venv` and prepare the complete host |
| `./dev.sh --chat` | Install the current checkout and start chat |
| `./dev.sh --check` | Check inference connectivity |
| `./dev.sh --diagnose` | Run detailed inference diagnostics |
| `./dev.sh --shell` | Open a shell with `.venv` activated |
| `./dev.sh --force-reinstall` | Reinstall the editable Python package |
| `./dev.sh --clean` | Recreate the virtual environment |

Running `./dev.sh` with no option prepares the local Python environment and
prints the next commands.

### Python CLI

Activate the environment first:

```bash
source .venv/bin/activate
```

| Command | Purpose |
|---|---|
| `lab-ai chat` | Start the interactive assistant |
| `lab-ai bootstrap` | Prepare host tools and inference |
| `lab-ai check` | Check the inference endpoint |
| `lab-ai setup` | Show the inference engine and setup script paths |
| `lab-ai version` | Show the package version |

## Troubleshooting

### LXD commands require permission

If bootstrap added your user to the `lxd` group, end the current login session
and log in again. Then verify:

```bash
lxc info
```

### Inference is unavailable

Run:

```bash
./dev.sh --diagnose
```

The diagnostic checks the snap, services, endpoint, model list, health, chat
API, and Python client.

Useful manual checks:

```bash
snap services gemma4
gemma4 status
sudo snap restart gemma4
./dev.sh --check
```

### The first message after a pause is slow

The inference snap can unload the model after an idle period. The assistant
reports that the model is reloading and retries the request.

To keep `gemma4` resident longer:

```bash
gemma4 get sleep-idle-seconds
gemma4 set sleep-idle-seconds=3600
```

### The model download takes a long time

Check snap activity:

```bash
snap changes
snap services gemma4
gemma4 status
```

For constrained hosts, select `e2b`:

```bash
bash scripts/install_inference_snap.sh --model e2b
```

### An LXD resource name already exists

The error lists conflicts such as:

```text
network:ca-f21a40ab-up
instance:demo-microcloud-node-1
volume:demo-microcloud-ceph-1-1
```

Use another prefix, or inspect and remove only resources you own. The assistant
will not adopt or delete an unknown resource automatically.

### `Missing Resource State After Create`

For a verified assistant-owned network, the deployment imports the missing
network state and continues once. Other resource types remain fail-closed.

If recovery is refused, inspect both systems before retrying:

```bash
cd terraform
tofu workspace list
TF_WORKSPACE=<workspace> tofu state list

lxc network list
lxc profile list
lxc list
lxc storage volume list default
```

### A deployment fails

Infrastructure operations are synchronous. If a failure is shown, no hidden
background job continues.

The assistant displays:

- the last deterministic error evidence
- an AI-generated root-cause analysis when available
- the safest suggested diagnostic or remediation

Do not run the same destructive command repeatedly without first reviewing the
reported state.

### Enable debug logging

```bash
export LOG_LEVEL=DEBUG
lab-ai --debug check
```

## Architecture

```text
src/lab_ai_assistant/
├── ai_engine.py       Local LLM, streaming, tools, and failure analysis
├── orchestrator.py    Agent loop, approval, locking, and execution
├── planning.py        Immutable plans and deterministic validation
├── sizing.py          Host-aware sizing
├── verification.py    State identity and cluster postconditions
├── doc_fetcher.py     Official documentation retrieval and caching
├── tools.py           Tool schemas and parameter validation
├── ui.py              Terminal user interface
└── cli.py             Command-line entry point

terraform/main.tf      LXD profiles, VMs, networks, and block volumes
playbooks/microcloud.yml
                       MicroCloud installation and cluster bootstrap
scripts/               Infrastructure lifecycle adapters and diagnostics
tests/                 Unit, contract, lifecycle, and safety tests
```

Runtime state is stored under:

```text
~/.canonical-ai-lab-assistant/
```

When installed as a snap in the future, `SNAP_USER_COMMON` is used instead.

## Development

Install development dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[dev]'
```

Run the checks:

```bash
python -m pytest -q
python -m ruff check src tests
python -m black --check src tests

for script in scripts/*.sh dev.sh; do
    bash -n "$script"
done

(cd terraform && tofu fmt -check -recursive && tofu validate)
ansible-playbook --syntax-check -i 'localhost,' playbooks/microcloud.yml
```

## Current limitations

- Production deployments are out of scope.
- Safe member removal and downscale are not automated.
- Network mode and dedicated CIDRs cannot be changed in place.
- Snap channels are configured in `playbooks/microcloud.yml`, not through chat.
- Custom MicroCloud preseed files are not exposed through chat.
- The supported delivery path is currently a source checkout using `dev.sh` or
  the Python CLI.

## License

GPL-3.0-or-later
