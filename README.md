# Canonical AI Lab Assistant

**AI-powered infrastructure automation for Canonical deployments**

An intelligent CLI assistant that uses AI to automate the deployment and management of MicroCloud and Canonical Kubernetes clusters. Talk to your infrastructure like you would to a person.

## Vision

Instead of learning complex CLI commands and scripts, users can simply chat with an AI agent:

```
You: Deploy me a 3 node microcloud setup with eth0 and lvm storage
Assistant: I'll deploy MicroCloud with 3 nodes. This will take about 10 minutes...
```

The AI handles:
- Parameter extraction and validation
- Documentation lookups
- Asking clarification questions
- Orchestrating deployment scripts
- Tracking deployment history

## Key Features

- 🤖 **Natural Language Interface**: Describe what you want, AI figures out the rest
- 🚀 **Multi-Scenario Support**: MicroCloud, Kubernetes Snap, Kubernetes+Juju
- 💭 **Smart Reasoning**: Uses Nemotron inference snap with step-by-step planning
- 📚 **Context-Aware**: References official documentation automatically
- ⚙️ **Safe Execution**: Asks for confirmation before destructive operations
- 💾 **History Tracking**: Maintains deployment history and state
- 🔌 **Modular**: Extensible tool system for adding new capabilities

## Architecture

```
┌─────────────────────────────────────────┐
│  CLI Interface (Typer)                  │
├─────────────────────────────────────────┤
│  AI Agent (Nemotron 3 Nano)             │
├─────────────────────────────────────────┤
│  Tool Executor                          │
│  ├─ orchestrate.sh (non-interactive)    │
│  ├─ Ansible playbooks                   │
│  └─ Terraform                           │
├─────────────────────────────────────────┤
│  Lab Automation Backend                 │
│  (from ismailkayi/lab-automation)       │
└─────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- Ubuntu 22.04 LTS or later
- Python 3.10+
- Access to ismailkayi/lab-automation repository

### Installation

```bash
# Clone repository
git clone https://github.com/ismailkayi/canonical-ai-lab-assistant.git
cd canonical-ai-lab-assistant

# Install inference engine
sudo snap install nemotron-3-nano

# Install project
pip install -e ".[dev]"

# Verify setup
lab-ai check
```

### First Deployment

```bash
# Start interactive chat
lab-ai chat

# Example conversation
You: Deploy 3 node microcloud
Assistant: I'll need to know:
  1. Which network interface? (e.g., eth0)
  2. Storage type? (lvm or ceph)
```

## Development

### Project Structure

```
canonical-ai-lab-assistant/
├── pyproject.toml              # Project configuration
├── README.md
├── src/lab_ai_assistant/
│   ├── __init__.py
│   ├── cli.py                  # CLI entry point
│   ├── config.py               # Configuration management
│   ├── ai_engine.py            # AI/LLM integration
│   ├── orchestrator.py         # Main orchestration logic
│   ├── tools.py                # Tool definitions
│   └── utils.py                # Utility functions
└── tests/
```

### Running Tests

```bash
pytest -v
pytest --cov=src/lab_ai_assistant
```

### Code Quality

```bash
# Format
black src/ tests/

# Lint
ruff check src/ tests/

# Type checking
mypy src/
```

## Supported Scenarios

### 1. MicroCloud Deployment

Deploy a 3-node MicroCloud cluster with shared storage and networking.

```
You: Deploy microcloud with 3 nodes
Assistant: [Asks for network interface and storage type]
```

### 2. Canonical Kubernetes (Snap)

Deploy Kubernetes using the k8s snap directly on VMs.

```
You: Deploy kubernetes with 1 control plane and 2 workers
Assistant: [Validates parameters and deploys]
```

### 3. Canonical Kubernetes (Juju)

Deploy Kubernetes using Juju controller for enterprise-grade management.

```
You: Deploy k8s with 3 control planes for high availability
Assistant: [Sets up Juju controller and deploys k8s bundle]
```

## Tool Definitions

Available AI tools:

- **deploy_microcloud**: Deploy MicroCloud clusters
- **deploy_k8s_snap**: Deploy Kubernetes via snap
- **deploy_k8s_juju**: Deploy Kubernetes via Juju
- **manage_lab**: Update, rebuild, or delete existing deployments
- **get_lab_status**: Query deployment status
- **list_workspaces**: Show all deployments
- **get_documentation**: Fetch relevant documentation

## Configuration

Environment variables (`.env`):

```env
INFERENCE_HOST=http://localhost:8000
INFERENCE_MODEL=nemotron-3-nano
LOG_LEVEL=INFO
```

State directory: `~/.lab-ai-assistant/`

## Roadmap

### Phase 1: Foundation (Current)
- ✅ CLI framework
- ✅ Tool definitions
- ✅ Basic orchestrate.sh wrapper
- ⏳ AI engine integration (Nemotron)

### Phase 2: AI Integration
- ⏳ Function calling with Nemotron
- ⏳ Multi-turn conversations
- ⏳ Confirmation flow

### Phase 3: Enhanced Features
- ⏳ Deployment history tracking
- ⏳ Error handling & recovery
- ⏳ Logging & debugging

### Phase 4: RAG & Documentation
- ⏳ Vector database for docs
- ⏳ Semantic search
- ⏳ Context-aware responses

### Phase 5: Snap Packaging
- ⏳ Snap manifest
- ⏳ Dependency bundling
- ⏳ Release automation

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and linting
5. Submit a pull request

## License

GPL-3.0-or-later

## References

- [Canonical Inference Snaps](https://documentation.ubuntu.com/inference-snaps/)
- [Lab Automation](https://github.com/ismailkayi/lab-automation)
- [MicroCloud](https://microcloud.io/)
- [Canonical Kubernetes](https://ubuntu.com/kubernetes/charm)

## Support

For issues and questions:
- GitHub Issues: [Report a bug](https://github.com/ismailkayi/canonical-ai-lab-assistant/issues)
- Discussions: [Ask a question](https://github.com/ismailkayi/canonical-ai-lab-assistant/discussions)

---

**Built with ❤️ for Canonical infrastructure automation**