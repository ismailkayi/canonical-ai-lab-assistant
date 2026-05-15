"""
Tool definitions for the MicroCloud AI agent.

These tools are what the LLM can call. The orchestrator executes them.
"""

from typing import Any


def get_tool_definitions() -> dict[str, Any]:
    """Return the full tool catalog used by the AI assistant."""
    return {
        "tools": [
            {
                "name": "prep_host",
                "description": (
                    "Prepare the Ubuntu host for local lab deployments. "
                    "Installs LXD, OpenTofu, Ansible, and base prerequisites."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
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
                            "description": "Snap name (default: nemotron-3-nano)",
                            "default": "nemotron-3-nano",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "inspect_host_environment",
                "description": (
                    "Inspect host capabilities before proposing topology: CPU, RAM, disks, "
                    "LXD networks, and storage pools. Always call this before sizing/deploy."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "get_documentation",
                "description": (
                    "Fetch official MicroCloud/LXD/MicroCeph documentation. "
                    "Use when uncertain or when user asks technical why/how questions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Topic key (e.g. microcloud, microcloud-networking, microceph)",
                        },
                        "url": {
                            "type": "string",
                            "description": "Direct URL override (optional)",
                        },
                    },
                    "required": ["topic"],
                },
            },
            {
                "name": "select_scenario",
                "description": (
                    "Choose a baseline scenario only if it fits clearly. "
                    "Allowed: standard, ha, no_ovn, custom."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "string",
                            "enum": ["standard", "ha", "no_ovn", "custom"],
                            "description": "Baseline scenario selection",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why this scenario is appropriate",
                        },
                    },
                    "required": ["scenario"],
                },
            },
            {
                "name": "propose_custom_topology",
                "description": (
                    "Design topology from scratch based on workload + host capacity. "
                    "Must include reasoning, trade-offs, and an alternative."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node_count": {"type": "integer"},
                        "node_cpu": {"type": "integer"},
                        "node_ram_gb": {"type": "integer"},
                        "root_disk_gb": {"type": "integer"},
                        "ceph_disk_gb": {"type": "integer"},
                        "use_ovn": {"type": "boolean", "default": True},
                        "ceph_replication_factor": {"type": "integer", "default": 3},
                        "reasoning": {"type": "string"},
                        "trade_offs": {"type": "string"},
                        "alternative": {"type": "string"},
                    },
                    "required": [
                        "node_count",
                        "node_cpu",
                        "node_ram_gb",
                        "ceph_disk_gb",
                        "reasoning",
                    ],
                },
            },
            {
                "name": "get_sizing_recommendation",
                "description": (
                    "Return per-node and total resource recommendation for a given workload."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "string",
                            "enum": ["standard", "ha", "no_ovn", "custom"],
                        },
                        "nodes": {"type": "integer"},
                        "workload_description": {"type": "string"},
                        "tier": {
                            "type": "string",
                            "enum": ["minimal", "small", "medium", "large"],
                        },
                    },
                    "required": ["scenario"],
                },
            },
            {
                "name": "deploy_microcloud",
                "description": (
                    "Deploy a cluster. Use after planning is complete and user explicitly confirms."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "string",
                            "enum": ["standard", "ha", "no_ovn", "custom"],
                            "default": "custom",
                        },
                        "user_prefix": {
                            "type": "string",
                            "description": "Name prefix for workspace/resources (e.g. ismail)",
                            "default": "lab",
                        },
                        "nodes": {"type": "integer", "default": 3},
                        "sizing_tier": {
                            "type": "string",
                            "enum": ["minimal", "small", "medium", "large", "conservative", "performance"],
                        },
                        "node_cpu": {"type": "integer"},
                        "node_memory_mb": {"type": "integer"},
                        "root_disk_gib": {"type": "integer"},
                        "ceph_disk_gib": {"type": "integer"},
                        "network_interface": {
                            "type": "string",
                            "description": "Cluster NIC (kept for plan traceability)",
                        },
                        "ovn_uplink_interface": {
                            "type": "string",
                            "description": "Dedicated no-IP OVN uplink NIC",
                        },
                        "ceph_osd_disk": {
                            "type": "string",
                            "description": "Dedicated unformatted OSD disk path",
                        },
                    },
                    "required": ["nodes"],
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
