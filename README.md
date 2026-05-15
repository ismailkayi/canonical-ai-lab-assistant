# Canonical AI Lab Assistant

**AI-powered infrastructure automation for Canonical deployments**































































 # Canonical AI Lab Assistant

 MicroCloud-first AI assistant for Canonical lab automation.

 This repository is self-contained. The host preparation logic, inference snap installation, and MicroCloud deployment scaffolding all live here, so there is no dependency on the older `lab-automation` repository for the first phase.

 ## Phase 1 Scope

 The first stage is intentionally narrow:

 - prepare the Ubuntu host
 - install the local inference snap
 - guide and plan a MicroCloud deployment
 - keep Kubernetes out of scope for now

 ## What Is Included

 - `scripts/prep_host.sh`: installs basic host prerequisites and can chain the inference install
 - `scripts/install_inference_snap.sh`: installs the Canonical inference snap
 - `scripts/deploy_microcloud.sh`: MicroCloud deployment plan scaffold for the assistant to call
 - `src/lab_ai_assistant/`: CLI, AI engine, tool definitions, and orchestration logic

 ## Quick Start

 ```bash
 git clone https://github.com/ismailkayi/canonical-ai-lab-assistant.git
 cd canonical-ai-lab-assistant
 pip install -e .
 ```

 ## Bootstrap Host

 ```bash
 lab-ai bootstrap
 ```

 That command runs the repo-local host prep script and installs the inference snap.

 ## Chat With The Assistant

 ```bash
 lab-ai chat
 ```

 Example:

 ```text
 You: Deploy a 3 node MicroCloud setup on eth0 with LVM storage
 Assistant: I need the storage size and whether you want to use the default preseed.
 ```

 ## Commands

 - `lab-ai check`: verify the local inference service is available
 - `lab-ai bootstrap`: prepare the host and install the inference snap
 - `lab-ai setup`: show the repo-local scripts and setup hints
 - `lab-ai chat`: start the interactive MicroCloud assistant

 ## Current Behavior

 The assistant is currently limited to MicroCloud-related tasks:

 - host preparation
 - inference snap installation
 - MicroCloud deployment planning

 Kubernetes support will be added later after the MicroCloud-only flow is stable.

 ## Project Layout

 ```text
 canonical-ai-lab-assistant/
 ├── README.md
 ├── pyproject.toml
 ├── scripts/
 │   ├── deploy_microcloud.sh
 │   ├── install_inference_snap.sh
 │   └── prep_host.sh
 └── src/
     └── lab_ai_assistant/
         ├── ai_engine.py
         ├── cli.py
         ├── config.py
         ├── orchestrator.py
         ├── tools.py
         └── utils.py
 ```

 ## Inference Snap

 The default engine is `nemotron-3-nano`. You can override it with environment variables:

 ```env
 INFERENCE_HOST=http://localhost:8000
 INFERENCE_MODEL=nemotron-3-nano
 LOG_LEVEL=INFO
 ```

 ## Next Step

 The next implementation step is to wire the MicroCloud deployment script to real deployment logic and keep the AI flow parameter-based instead of menu-based.