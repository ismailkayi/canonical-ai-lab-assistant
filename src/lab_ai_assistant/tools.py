"""
Tool definitions for the MicroCloud-first AI agent.

Tools are the vocabulary the LLM uses to request actions.  The agent
picks a tool + parameters; the orchestrator executes it.
"""

from typing import Any


def get_tool_definitions() -> dict[str, Any]:
    """Return the full tool catalog used by the AI assistant."""
    return {
        "tools": [
            # ----------------------------------------------------------------
            # Host preparation
            # ----------------------------------------------------------------
            {
                "name": "prep_host",
                "description": (
                    "Prepare the Ubuntu host: install prerequisite packages and "
                    "optionally install the inference snap."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "install_inference": {
                            "type": "boolean",
                            "description": "Also install the inference snap",
                            "default": True,
                        },
                        "install_microcloud_prereqs": {
                            "type": "boolean",
                            "description": "Install packages needed for MicroCloud work",
                            "default": True,
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "install_inference_snap",
                "description": "Install the Canonical inference snap used by this assistant.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "engine": {
                            "type": "string",
                            "description": "Snap name (e.g. nemotron-3-nano)",
                            "default": "nemotron-3-nano",
                        }
                    },
                    "required": [],
                },
            },
            # ----------------------------------------------------------------
            # Scenario selection and sizing
            # ----------------------------------------------------------------
            {
                "name": "select_scenario",
                "description": (
                    "Analyse the user's intent and select the most appropriate "
                    "MicroCloud deployment scenario. Returns the scenario definition "
                    "including required parameters the user must still provide."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "string",
                                "enum": ["standard", "ha", "no_ovn", "custom"],
                            "description": (
                                "Scenario name: "
                                    "standard (3-node, OVN + Ceph — default), "
                                    "ha (5-node, OVN + Ceph, HA/production), "
                                    "no_ovn (3-node, Ceph only, no OVN — explicit user opt-out), "
                                    "custom (AI reasons from scratch about the topology)"
                            ),
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief rationale for the selection",
                        },
                    },
                    "required": ["scenario"],
                },
            },
            {
                "name": "get_sizing_recommendation",
                "description": (
                    "Return a per-node and total resource recommendation "
                    "(CPU, RAM, disk) for the selected scenario and workload."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "string",
                                "enum": ["standard", "ha", "no_ovn", "custom"],
                        },
                        "nodes": {
                            "type": "integer",
                            "description": "Intended node count",
                        },
                        "workload_description": {
                            "type": "string",
                            "description": (
                                "Free-text description of the workload, "
                                "e.g. 'staging for 10 developers', 'production k8s'"
                            ),
                        },
                        "tier": {
                            "type": "string",
                            "enum": ["minimal", "small", "medium", "large"],
                            "description": "Force a specific sizing tier (optional)",
                        },
                    },
                    "required": ["scenario"],
                },
            },
            # ----------------------------------------------------------------
            # Deployment
            # ----------------------------------------------------------------
            {
                "name": "deploy_microcloud",
                "description": (
                    "Deploy a MicroCloud cluster with all parameters validated. "
                    "Call this only after scenario + sizing are agreed with the user."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "string",
                                "enum": ["standard", "ha", "no_ovn", "custom"],
                            "description": "Deployment scenario",
                        },
                        "nodes": {
                            "type": "integer",
                            "description": "Number of nodes",
                            "default": 3,
                        },
                        "sizing_tier": {
                            "type": "string",
                            "enum": ["minimal", "small", "medium", "large"],
                            "description": "Resource tier per node",
                            "default": "small",
                        },
                        "network_interface": {
                            "type": "string",
                            "description": "Network interface for cluster traffic (e.g. eth0)",
                        },
                        "ovn_uplink_interface": {
                            "type": "string",
                                "description": "NIC for OVN uplink — must have no IP (required for standard and ha)",
                        },
                        "ceph_osd_disk": {
                            "type": "string",
                                "description": "Block device path for Ceph OSD — must be unformatted (required by all scenarios)",
                        },
                        "ipv4_gateway": {
                            "type": "string",
                            "description": "IPv4 gateway for OVN uplink (e.g. 10.10.10.1/24)",
                        },
                        "ipv4_range": {
                            "type": "string",
                            "description": "IPv4 allocation range for OVN (e.g. 10.10.10.100-10.10.10.200)",
                        },
                        "preseed_file": {
                            "type": "string",
                            "description": "Path to a custom microcloud preseed YAML (optional)",
                        },
                    },
                    "required": ["scenario", "nodes", "network_interface", "storage_disk"],
                                    "required": ["scenario", "nodes", "network_interface", "ceph_osd_disk"],
                },
            },
            # ----------------------------------------------------------------
            # Documentation
            # ----------------------------------------------------------------
            {
                "name": "get_documentation",
                "description": (
                    "Fetch content from official MicroCloud / LXD / MicroCeph "
                    "documentation. Use this when you need to answer a question "
                    "about requirements, networking, storage, or best practices."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": (
                                "Topic or doc key, e.g.: "
                                "microcloud, microcloud-networking, microcloud-storage, "
                                "microcloud-requirements, microcloud-preseed, "
                                "lxd, microceph, inference-snaps"
                            ),
                        },
                        "url": {
                            "type": "string",
                            "description": "Fetch a specific URL directly (optional, overrides topic)",
                        },
                    },
                    "required": ["topic"],
                },
            },
                # ----------------------------------------------------------------
                # AI-designed topology (no predefined scenario)
                # ----------------------------------------------------------------
                {
                    "name": "propose_custom_topology",
                    "description": (
                        "The AI designs a deployment topology from scratch based on "
                        "the user's workload, team size, availability requirements, "
                        "and host resources. Use this instead of select_scenario when "
                        "no standard scenario fits, or when the user wants the AI to "
                        "think freely. The AI must provide full reasoning."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "node_count": {
                                "type": "integer",
                                "description": "Proposed number of nodes (must be odd, >= 3)",
                            },
                            "node_cpu": {
                                "type": "integer",
                                "description": "Proposed vCPU count per node",
                            },
                            "node_ram_gb": {
                                "type": "integer",
                                "description": "Proposed RAM in GB per node",
                            },
                            "root_disk_gb": {
                                "type": "integer",
                                "description": "Proposed root disk in GB per node",
                            },
                            "ceph_disk_gb": {
                                "type": "integer",
                                "description": "Proposed Ceph OSD disk in GB per node",
                            },
                            "use_ovn": {
                                "type": "boolean",
                                "description": "Whether to include MicroOVN (default: true)",
                                "default": True,
                            },
                            "ceph_replication_factor": {
                                "type": "integer",
                                "description": "Ceph replication factor (2 or 3, default: 3)",
                                "default": 3,
                            },
                            "reasoning": {
                                "type": "string",
                                "description": (
                                    "Full explanation of WHY this topology was chosen: "
                                    "workload needs, HA considerations, host resources, "
                                    "trade-offs compared to alternatives."
                                ),
                            },
                            "trade_offs": {
                                "type": "string",
                                "description": "What this topology gives up vs a larger or smaller setup",
                            },
                            "alternative": {
                                "type": "string",
                                "description": "Brief description of the next-best alternative topology",
                            },
                        },
                        "required": ["node_count", "node_cpu", "node_ram_gb", "ceph_disk_gb", "reasoning"],
                    },
                },
        ]
    }


def get_tool_by_name(name: str) -> dict[str, Any] | None:
    """Return a tool definition by name, or None if not found."""
    for tool in get_tool_definitions()["tools"]:
        if tool["name"] == name:
            return tool
    return None


def validate_tool_parameters(tool_name: str, parameters: dict[str, Any]) -> tuple[bool, str]:
    """Validate that all required parameters for a tool are present."""
    tool = get_tool_by_name(tool_name)
    if not tool:
        return False, f"Unknown tool: '{tool_name}'"

    required = tool["parameters"].get("required", [])
    for param in required:
        if param not in parameters:
            return False, f"Missing required parameter: '{param}'"

    return True, ""
