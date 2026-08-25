# Canonical AI Lab Assistant

An AI-first assistant for creating and managing local MicroCloud lab, demo,
training, and proof-of-concept environments.

Describe the environment in natural language. The assistant inspects the host,
proposes a topology, validates it against live capacity, asks you to approve the
exact plan, runs OpenTofu and Ansible, and verifies the resulting cluster.

This project is intended for local learning and demonstration environments. It
is not a production deployment tool.

## Key Capabilities

- Host-aware topology and sizing recommendations
- LXD virtual machines provisioned with OpenTofu
- MicroCloud, LXD, MicroCeph, and MicroOVN configured with Ansible
- One to eight Ceph OSD disks per node
- Optional local ZFS storage alongside distributed Ceph
- Environment listing, expansion, health verification, and cleanup
- Current official Canonical documentation retrieval
- Recovery from known transient inference and LXD-provider failures

## How It Works

```text
User request
    |
    v
Live host and environment observation
    |
    v
AI planning and tool selection
    |
    v
Schema, capability, capacity, and state validation
    |
    v
Exact plan display and one-time approval
    |
    v
OpenTofu infrastructure + Ansible configuration
    |
    v
Evidence-based cluster verification
    |
    v
AI explanation or remediation proposal
```

The AI plans and explains. Python owns live facts, validation, approval binding,
locking, and verification. Shell scripts, OpenTofu, and Ansible apply only the
approved plan. Everything runs locally through LXD VMs; no public cloud account
is required.

## Prerequisites

- Ubuntu host (24.04 recommended)
- Hardware virtualization available to LXD
- Internet access for snaps, images, and current documentation
- Python 3.10 or newer
- A user with `sudo` access
- Enough CPU, RAM, and storage for the requested nested VMs

Bootstrap installs or prepares snapd, LXD, OpenTofu, Ansible, an SSH key, and
the local inference snap (`gemma4` by default).

## Recommended Setup (`dev.sh`)

Use this path unless you specifically want to manage the Python virtual
environment yourself. `dev.sh` creates or repairs `.venv`, installs the current
checkout in editable mode, and starts the requested command.

### First Time on a Host

```bash
git clone https://github.com/ismailkayi/canonical-ai-lab-assistant.git
cd canonical-ai-lab-assistant

# One-time host preparation: Python environment, LXD, OpenTofu, Ansible,
# SSH key, and the local inference snap.
./dev.sh --bootstrap
```

If bootstrap adds your user to the `lxd` group, log out and back in before
continuing. Bootstrap does not need to be repeated on every run.

Start the assistant:

```bash
./dev.sh --chat
```

### Normal Daily Use

From the repository directory, run:

```bash
./dev.sh --chat
```

Use the check command only when diagnosing inference connectivity:

```bash
./dev.sh --check
```

After pulling new source code, `./dev.sh --chat` automatically reinstalls the
current checkout before opening the chat.

## Alternative: Manual Python Setup

Use this path instead of `dev.sh` only if you prefer to create, activate, and
update the virtual environment yourself. It runs the same `lab-ai` application.

### First Time on a Host

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .

# One-time host preparation.
lab-ai bootstrap

# Verify inference and start the assistant.
lab-ai check
lab-ai chat
```

If bootstrap adds your user to the `lxd` group, log out and back in. Then
reactivate the virtual environment before using `lab-ai`.

### Normal Daily Use

```bash
cd canonical-ai-lab-assistant
source .venv/bin/activate
lab-ai chat
```

After pulling new source code, update the editable installation once:

```bash
source .venv/bin/activate
python -m pip install -e .
```

## Example Chat Workflow

```text
You: Deploy a fresh 3-node MicroCloud demo environment. Use 2 vCPU,
     6 GB RAM, a 30 GB root disk, and two 20 GB Ceph disks per node.

AI:  [Displays the exact resolved plan and Plan ID.]

You: yes

AI:  [Deploys, verifies the cluster, and reports access and health evidence.]
```

Other useful requests:

```text
List deployed environments.
Check the health of lab_microcloud.
Add one node to lab_microcloud.
Scale lab_microcloud to five nodes.
Delete lab_microcloud.
Use the current official MicroCloud requirements documentation to explain the
recommended node count.
```

## CLI Commands

| Command | Description |
|---|---|
| `lab-ai chat` | Start the interactive assistant |
| `lab-ai bootstrap` | Prepare host tools and install the inference snap |
| `lab-ai check` | Check the configured inference endpoint |
| `lab-ai setup` | Show important runtime paths |
| `lab-ai version` | Show installed version |

## Configuration

Set environment variables via `.env` (or shell):

```env
INFERENCE_HOST=http://127.0.0.1:8336
INFERENCE_MODEL=gemma4
INFERENCE_TIMEOUT_SEC=120
INFERENCE_RESTART_TIMEOUT_SEC=15
INFERENCE_MAX_RETRIES=3
OPERATION_TIMEOUT_SEC=3600
LOG_LEVEL=INFO
```

`INFERENCE_RESTART_TIMEOUT_SEC` controls how long the client waits for a local
inference restart. `OPERATION_TIMEOUT_SEC` limits script-backed infrastructure
operations. `INFERENCE_MAX_RETRIES` controls retry attempts for transient
disconnects; lower it (for example `1`) to fail faster when a long request is
timing out.

## Plan and Approval Safety

Every infrastructure-changing action uses this flow:

1. Resolve all required parameters.
2. Validate capabilities and residual host capacity.
3. Display the exact environment, state identity, node transition, storage
        pool, resource totals, parameters, and Plan ID.
4. Require a standalone confirmation such as `yes`.
5. Revalidate host and Terraform state while holding the shared lock.
6. Apply a saved OpenTofu plan.
7. Verify the requested cluster state independently.

Changing the plan, host capacity, or target state invalidates the approval.

## Deployment Options

| Option | Supported values |
|---|---|
| Nodes | 3-50 |
| vCPU per node | 1 or more |
| Memory per node | 1024 MiB or more |
| Root disk | 20 GiB or more |
| Ceph OSD disk | 10 GiB or more |
| Ceph disks per node | 1-8 |
| Local ZFS disk | 0 to disable, otherwise 10 GiB or more |
| Networking | MicroOVN with a dedicated virtual uplink |
| Image | Ubuntu 24.04 by default |

MicroCloud runs inside LXD VMs. Ceph and optional local disks are virtual block
volumes created in the selected LXD storage pool.

## Deployment Behavior

When you confirm a deployment, the assistant:

1. Detects the LXD bridge and storage pool.
2. Resolves and validates all node resources.
3. Creates a new OpenTofu workspace and saved plan.
4. Creates VMs, networks, and block volumes.
5. Runs `ansible-playbook playbooks/microcloud.yml`.
6. Verifies exact membership, Ceph health, and OSD count.
7. Reports node names, IP addresses, UI URLs, and health evidence.

Supported node counts are 3-50. Even and odd sizes are supported; topology and
failure-tolerance requirements should still match the intended demonstration.

## Lifecycle Operations

- **Fresh deploy:** refuses to modify an existing managed environment. An empty
        stale workspace is recycled safely.
- **List:** reports active environments, node counts, and running state.
- **Add nodes:** preserves the existing image, CPU, RAM, storage geometry,
        network, storage pool, and SSH authorization.
- **Scale:** expands to a larger total using the live add-member workflow.
- **Downscale:** not implemented because members and Ceph OSDs must be drained
  before VM destruction.
- **Health:** verifies exact membership in MicroCloud, LXD, MicroCeph, and
  MicroOVN, plus Ceph health and expected OSD count.
- **Delete:** destroys the approved saved plan and removes the workspace and
  generated inventory.

Environments created before versioned deployment specifications were introduced
must be deleted and created fresh once before add/scale operations are available.

## Documentation Grounding

Live observations are authoritative for local host and cluster state. For
current or version-sensitive product behavior, the assistant can retrieve
official Canonical documentation from approved HTTPS hosts. It extracts the
main article, records source and retrieval time, caches successful responses,
and uses Canonical upstream documentation as a fallback.

## Development Checks

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'

python -m ruff format --check src tests
python -m ruff check src tests
python -m mypy src
pytest -q

for script in scripts/*.sh dev.sh; do bash -n "$script"; done
(cd terraform && tofu fmt -check -recursive && tofu validate -no-color)
ansible-playbook --syntax-check -i 'localhost,' playbooks/microcloud.yml
```

## Repository Layout

```text
canonical-ai-lab-assistant/
|- dev.sh                         # Local setup and chat launcher
|- scripts/                       # Infrastructure execution adapters
|- playbooks/microcloud.yml       # Guest setup and cluster bootstrap
|- terraform/main.tf              # LXD VMs, networks, and block volumes
|- src/lab_ai_assistant/
|  |- ai_engine.py                # Local LLM and native tool protocol
|  |- orchestrator.py             # Agent loop, locking, and execution
|  |- planning.py                 # Plans, validation, and approval
|  |- verification.py             # Cluster postcondition evidence
|  |- doc_fetcher.py              # Official documentation retrieval
|  |- sizing.py                    # Host-aware sizing
|  |- tools.py                     # Tool schemas and validation
|  `- cli.py                       # Typer CLI
`- tests/                          # Planning, lifecycle, and safety tests
```

## Troubleshooting

### Inference endpoint is unavailable

**Symptom:** `Error: Inference engine not available at http://127.0.0.1:8336`

#### Quick Diagnostic

Run the comprehensive diagnostic script to identify the exact issue:

```bash
./scripts/diagnose_inference.sh
```

This checks:
- Snap installation status
- Service status
- Port availability
- Health endpoint
- Available models
- Chat endpoint
- Python client connectivity

#### Common Solutions

**1. Snap services not running:**
```bash
snap services gemma4
sudo snap restart gemma4
lab-ai check
```

**2. Snap on different machine:**

If you see different hostnames between snap installation and CLI execution:

```bash
# Find the snap machine's IP
export INFERENCE_HOST="http://<snap_host_ip>:8336"
lab-ai check
```

Or save to `.env`:
```bash
cat > .env << 'EOF'
INFERENCE_HOST=http://<snap_host_ip>:8336
INFERENCE_MODEL=gemma4
EOF
```

**3. Model name mismatch:**

The configured model may need adjustment based on installed version:

```bash
# Check available models
curl http://127.0.0.1:8336/v1/models | jq '.data[].id'

# Update the model name
export INFERENCE_MODEL=gemma4-e4b-q4-k-m
lab-ai check
```

**4. Timeout or connection issues:**

Increase the timeout if the inference snap is slow to respond:

```bash
export INFERENCE_TIMEOUT_SEC=60
export INFERENCE_RESTART_TIMEOUT_SEC=20
export INFERENCE_MAX_RETRIES=1
lab-ai check
```

**5. Enable debug logging:**

```bash
export LOG_LEVEL=DEBUG
lab-ai --debug check
```

The client waits for the health endpoint and retries after a transient local
inference restart.

### `Missing Resource State After Create`

Some LXD provider versions can return a transient state error after resource
creation. The deployment serializes creation and performs one controlled retry
only for this known error.

### An environment name already exists

Fresh deploys do not resize existing environments. Add/scale the existing lab,
choose another prefix, or delete it first. A workspace with no state or managed
resources is stale and removed automatically.

### An operation failed

Script-backed operations are synchronous. If a failure is reported, no hidden
background job continues. Correct the reported cause and prepare a new plan.

## Current Limitations

- Safe member removal and downscale are not implemented.
- Snap channels are configured in `playbooks/microcloud.yml`, not through chat.
- Custom preseed files are not exposed through the chat interface.
- Network changes after deployment are not automated.
- Snap packaging is a future delivery target; the current supported workflow is
        a source checkout using `dev.sh` or the Python CLI.

## License

GPL-3.0-or-later
