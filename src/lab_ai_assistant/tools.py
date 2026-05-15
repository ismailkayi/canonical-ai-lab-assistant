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
                            "enum": ["minimal", "standard", "ha", "custom"],
                            "description": (
                                "Scenario name: "
                                "minimal (3-node, no OVN, LVM, PoC/dev), "
                                "standard (3-node, OVN, LVM, typical lab), "
                                "ha (5-node, OVN, Ceph, production-like), "
                                "custom (user-defined everything)"
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
                            "enum": ["minimal", "standard", "ha", "custom"],
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
                            "enum": ["minimal", "standard", "ha", "custom"],
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
                            "description": "NIC for OVN uplink (required for standard and ha scenarios)",
                        },
                        "storage_disk": {
                            "type": "string",
                            "description": "Block device path for LVM storage pool (e.g. /dev/sdb)",
                        },
                        "ceph_osd_disk": {
                            "type": "string",
                            "description": "Block device path for Ceph OSD (required for ha scenario)",
                        },
                        "storage_size": {
                            "type": "string",
                            "description": "Storage pool size (e.g. 50GB). Ignored when a full disk is given.",
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
