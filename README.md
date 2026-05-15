# Canonical AI Lab Assistant

AI-powered MicroCloud deployment assistant for Canonical lab environments.

---

## What does this do?

You type what you want in plain language. The assistant figures out the details, asks follow-up questions if something is missing, and then creates a real MicroCloud cluster for you — automatically.

```
You: I need a small 3-node test cluster
Assistant: Got it. Based on your host (32 CPU / 128 GB RAM) I recommend the
           "standard" scenario with 4 vCPU / 16 GB RAM per node.
           What user prefix should I use? (e.g. "alice")

You: alice
Assistant: Here's the plan:
           - 3 LXD virtual machines (alice-node-1, alice-node-2, alice-node-3)
           - MicroCloud with OVN networking and Ceph storage
           - ~12 vCPU / 48 GB RAM / 150 GB Ceph total
           Shall I proceed? [yes/no]

You: yes
Assistant: Provisioning... done. Cluster is healthy.
           alice-node-1  10.88.0.11  https://10.88.0.11:8443
           alice-node-2  10.88.0.12  https://10.88.0.12:8443
           alice-node-3  10.88.0.13  https://10.88.0.13:8443
```

No need to know Terraform, Ansible, or MicroCloud internals.

---

## Architecture overview

```
Your terminal
    │
    ▼
lab-ai chat  (Python CLI)
    │
    ▼
AI engine  ←─── local LLM (Nemotron 3 Nano snap)
    │             understands plain language, picks scenario,
     │             designs topologies, calculates sizing,
     │             reasons about trade-offs
    ▼
Orchestrator
    │
     ├── select_scenario            (standard / ha / no_ovn)
     ├── propose_custom_topology    (AI designs from scratch, shows reasoning)
     ├── get_sizing_recommendation  (CPU / RAM / disk per node)
     ├── get_documentation          (fetches official Ubuntu docs on demand)
    │
    └── deploy_microcloud.sh
            │
            ├── OpenTofu (terraform/)
            │     └── Creates LXD virtual machines on your host
            │           - 3–7 Ubuntu 24.04 VMs
            │           - OVN uplink bridge (second NIC per VM)
            │           - Ceph block volume per VM
            │
            └── Ansible (playbooks/microcloud.yml)
                  └── Inside those VMs:
                        - Installs microcloud, microceph, microovn, lxd snaps
                        - Auto-detects NIC and disk on each node
                        - Runs `microcloud preseed` to bootstrap the cluster
```

Everything runs locally. No cloud provider, no internet required after setup.

---

## Deployment scenarios

MicroCloud always uses **MicroCeph** for storage and **MicroOVN** for networking.
There is no LVM option — every node needs a dedicated, unformatted disk for Ceph OSD.

| Scenario | Nodes | Networking | Storage | Use case |
|---|---|---|---|---|
| `standard` | 3 | OVN | Ceph | Normal lab (default) |
| `ha` | 5 | OVN | Ceph | Production / staging, 2-node fault tolerance |
| `no_ovn` | 3 | none | Ceph | Only if user explicitly skips OVN |
| `custom` | any (odd, ≥3) | OVN | Ceph | AI designs the topology from scratch |

---

## Prerequisites

- Ubuntu 24.04 host
- At least 24 CPU cores and 48 GB RAM (for a 3-node standard cluster)

LXD, OpenTofu, and Ansible are installed automatically by `lab-ai bootstrap`.

---

## Quick start

```bash
git clone https://github.com/ismailkayi/canonical-ai-lab-assistant.git
cd canonical-ai-lab-assistant
pip install -e .

# Prepare host: installs OpenTofu, Ansible, SSH key, initialises Terraform
lab-ai bootstrap

# Start the assistant
lab-ai chat
```

---

## CLI commands

| Command | What it does |
|---|---|
| `lab-ai chat` | Start the interactive assistant |
| `lab-ai bootstrap` | Prepare the host (LXD, OpenTofu, Ansible, SSH key) |
| `lab-ai check` | Check that the local LLM is running |
| `lab-ai setup` | Show config and script paths |
| `lab-ai version` | Print version |

---

## Configuration

Copy `.env.example` to `.env` and adjust if needed:

```env
INFERENCE_HOST=http://localhost:8000
INFERENCE_MODEL=nemotron-3-nano
LOG_LEVEL=INFO
```

The default engine is the **Canonical Nemotron 3 Nano inference snap** — a local,
offline LLM that runs entirely on your machine.

---

## Repository layout

```
canonical-ai-lab-assistant/
│
├── terraform/
│   └── main.tf                  # LXD VMs, OVN bridge, Ceph volumes
│
├── playbooks/
│   └── microcloud.yml           # MicroCloud bootstrap via Ansible
│
├── scripts/
│   ├── prep_host.sh             # Install LXD, OpenTofu, Ansible, SSH key
│   ├── deploy_microcloud.sh     # Full deploy: sizing → tofu apply → ansible
│   └── install_inference_snap.sh
│
└── src/lab_ai_assistant/
    ├── cli.py                   # lab-ai commands (chat, bootstrap, check…)
    ├── ai_engine.py             # Nemotron API + system prompt
    ├── orchestrator.py          # Connects AI decisions to script execution
    ├── scenarios.py             # Scenario catalog (minimal/standard/ha/custom)
    ├── sizing.py                # Resource sizing advisor
    ├── doc_fetcher.py           # Fetches official Ubuntu docs on demand
    └── tools.py                 # Tool definitions the LLM can call
```

---

## How a deployment works step by step

1. You describe what you need (`lab-ai chat`)
2. The AI reads your message and calls `select_scenario` internally → picks minimal / standard / ha / custom
3. The AI calls `get_sizing_recommendation` → calculates CPU / RAM / disk per node based on your host resources
4. If any required parameter is missing (user prefix, node count) the AI asks you
5. You confirm the plan
6. `deploy_microcloud.sh` runs:
   - Detects your LXD bridge and storage pool automatically
   - Runs `tofu apply` → creates LXD VMs
   - Runs `ansible-playbook microcloud.yml` → installs snaps inside VMs and bootstraps the cluster
7. You get a summary with node IPs and LXD UI links
