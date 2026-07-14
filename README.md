# Canonical AI Lab Assistant

AI-powered MicroCloud lifecycle assistant for local lab and demo environments.

It translates plain-language requests into real infrastructure actions on your host:

- Host inspection and sizing recommendations
- Cluster deployment (OpenTofu + Ansible)
- Environment listing, scaling, node addition, health checks, and cleanup
- Documentation lookups with resilient fallback sources

## What It Does

You can ask for operations naturally, for example:

- "List deployed environments"
- "Deploy a 3-node setup with 2 OSDs per node and 20GB local disk"
- "Add 1 node to lab_microcloud"
- "Check cluster health"
- "Delete lab_microcloud"

The assistant executes tools synchronously and reports the final outcome. It does not rely on hidden background jobs for script-backed operations.

## Architecture

```text
Terminal (lab-ai chat)
        |
        v
AI Engine (local inference endpoint)
        |
        v
Orchestrator (tool loop + safety gates)
        |
        +--> scripts/deploy_microcloud.sh
        |        |- OpenTofu (terraform/main.tf)
        |        '- Ansible (playbooks/microcloud.yml)
        |
        +--> scripts/add_cluster_node.sh
        +--> scripts/scale_microcloud.sh
        +--> scripts/list_microcloud_environments.sh
        +--> scripts/verify_cluster_health.sh
        '--> scripts/cleanup_microcloud.sh
```

Everything runs on your host through LXD-based VMs. No public cloud account is required.

## Prerequisites

- Ubuntu host (24.04 recommended)
- LXD-capable machine with enough CPU, RAM, and storage for your intended topology
- Python 3.10+

The bootstrap flow installs runtime dependencies such as snapd, LXD, OpenTofu, and Ansible.

## Quick Start

```bash
git clone https://github.com/ismailkayi/canonical-ai-lab-assistant.git
cd canonical-ai-lab-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Optional: prepare host dependencies
lab-ai bootstrap

# Start chat
lab-ai chat
```

If you use the helper script:

```bash
./dev.sh --chat
```

## CLI Commands

| Command | Description |
|---|---|
| `lab-ai chat` | Start interactive assistant mode |
| `lab-ai bootstrap` | Install and prepare host tooling |
| `lab-ai check` | Validate inference endpoint availability |
| `lab-ai setup` | Print key script/config locations |
| `lab-ai version` | Show installed version |

## Configuration

Set environment variables via `.env` (or shell):

```env
INFERENCE_HOST=http://127.0.0.1:8336
INFERENCE_MODEL=gemma4
INFERENCE_TIMEOUT_SEC=120
LOG_LEVEL=INFO
```

## Deployment Behavior

When you confirm a deployment, the assistant:

1. Detects LXD defaults (bridge and storage pool)
2. Calculates or applies requested sizing
3. Runs `tofu apply` (serialized for provider stability)
4. Runs `ansible-playbook playbooks/microcloud.yml`
5. Prints cluster access details (node names, IPs, UI URLs)

Supported node counts in this automation are `>= 3`.

Note on quorum guidance:

- Even and odd cluster sizes are supported.
- For HA characteristics and failure tolerance, topology choices still matter and should match your operational goals.

## Lifecycle Operations

The assistant can manage existing environments end-to-end:

- List environments with running node counts
- Scale an environment to a new target size (`>= 3`)
- Add one or more nodes to a live cluster
- Verify MicroCloud/LXD/MicroCeph/MicroOVN health
- Destroy an environment and remove Terraform workspace artifacts

Fresh deploys never reuse an existing OpenTofu workspace. This prevents an
accidental deploy request from resizing or destroying members of a running lab.
Environments created before versioned deployment specifications were introduced
must be deleted and created fresh once before add/scale operations are available.

## Documentation Fetching

The documentation tool first tries official Ubuntu documentation URLs.
If access is blocked (for example by Cloudflare 403 challenge), it falls back to canonical upstream documentation sources (GitHub raw docs) so guidance remains available.

## Repository Layout

```text
canonical-ai-lab-assistant/
├── scripts/
│   ├── deploy_microcloud.sh
│   ├── add_cluster_node.sh
│   ├── scale_microcloud.sh
│   ├── cleanup_microcloud.sh
│   ├── verify_cluster_health.sh
│   └── list_microcloud_environments.sh
├── playbooks/
│   └── microcloud.yml
├── terraform/
│   └── main.tf
└── src/lab_ai_assistant/
    ├── ai_engine.py
    ├── orchestrator.py
    ├── doc_fetcher.py
    ├── tools.py
    └── cli.py
```

## Troubleshooting

### 1) `Missing Resource State After Create`

This is a known provider-side race behavior with some LXD/OpenTofu resource creations.
The current deployment flow uses serialized apply execution to reduce this risk.

### 2) Docs fetch returns 403

If `documentation.ubuntu.com` blocks the request, the assistant automatically switches to fallback sources.

### 3) Tool said it would do something, but nothing ran

Script-backed tools are synchronous. If a script fails, the assistant now reports failure explicitly rather than implying background execution.

## License

GPL-3.0-or-later
