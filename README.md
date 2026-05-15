# Canonical AI Lab Assistant

AI-powered MicroCloud deployment assistant for Canonical lab environments.

---

## What does this do?

You type what you want in plain language. The assistant figures out the details, asks follow-up questions if something is missing, and then creates a real MicroCloud cluster for you — automatically.

```
You: I need a small 3-node test cluster
Assistant: Got it. Based on your host (32 CPU / 128 GB RAM), I propose a
           3-node custom topology with 4 vCPU / 16 GB RAM per node.
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
    │             understands plain language,
    │             inspects host capacity,
    │             designs topology with trade-offs
    ▼
Orchestrator
    │
    ├── inspect_host_environment    (CPU / RAM / disks / LXD facts)
    ├── propose_custom_topology     (AI designs from scratch)
    ├── get_sizing_recommendation   (CPU / RAM / disk per node)
    ├── get_documentation           (official docs when needed)
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
                        - Runs microcloud preseed to bootstrap the cluster
```

Everything runs locally. No cloud provider is required.

---

## Planning model

There are no fixed baseline scenarios in the decision flow.
The assistant always plans topology from scratch based on:

- workload intent
- host capacity (CPU/RAM/disk/LXD facts)
- availability target
- cost/performance trade-offs

MicroCloud facts remain fixed:

- storage is always **MicroCeph**
- OVN is default unless explicitly disabled by user request
- Ceph needs dedicated, unformatted OSD disks per node

---

## Prerequisites

- Ubuntu 24.04 host
- At least 24 CPU cores and 48 GB RAM (for a 3-node standard cluster)

`lab-ai bootstrap` installs host runtime dependencies automatically
(snapd, LXD, OpenTofu, Ansible, SSH key setup).
It is intentionally focused on runtime host requirements, not Python dev tooling.

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
    ├── scenarios.py             # Planning primitives (custom-topology mode)
    ├── sizing.py                # Resource sizing advisor
    ├── doc_fetcher.py           # Fetches official Ubuntu docs on demand
    └── tools.py                 # Tool definitions the LLM can call
```

---

## How deployment planning works

1. You describe what you need in plain language.
2. The AI inspects the host with inspect_host_environment.
3. The AI may fetch docs with get_documentation if requirements are unclear.
4. The AI proposes a custom topology (node count, sizing, OVN mode).
5. The AI explains reasoning, trade-offs, and one alternative.
6. If required deployment parameters are missing, it asks follow-up questions.
7. After explicit confirmation, deploy_microcloud.sh runs:
   - Detects your LXD bridge and storage pool automatically
   - Runs `tofu apply` → creates LXD VMs
   - Runs `ansible-playbook microcloud.yml` → installs snaps inside VMs and bootstraps the cluster
8. You get a summary with node IPs and LXD UI links
