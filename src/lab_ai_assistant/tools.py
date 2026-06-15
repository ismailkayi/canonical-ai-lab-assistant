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
                            "description": "Snap name (default: gemma4)",
                            "default": "gemma4",
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
                "name": "propose_custom_topology",
                "description": (
                    "Design topology from scratch based on workload + host capacity. "
                    "Must include reasoning, trade-offs, and an alternative."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "string",
                            "enum": ["custom"],
                            "default": "custom",
                        },
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
                        "scenario": {"type": "string", "enum": ["custom"], "default": "custom"},
                        "nodes": {"type": "integer"},
                        "workload_description": {"type": "string"},
                        "tier": {
                            "type": "string",
                            "enum": ["minimal", "small", "medium", "large"],
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "deploy_microcloud",
                "description": (
                    "Deploy a full MicroCloud lab cluster. Runs OpenTofu (creates VMs, "
                    "network, storage) then Ansible (installs snaps, initializes cluster). "
                    "Both phases always execute together. Use after user explicitly confirms."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "string",
                            "enum": ["custom"],
                            "default": "custom",
                        },
                        "user_prefix": {
                            "type": "string",
                            "description": "Short word prefix for resource naming, e.g. 'lab' or 'alice'. Do NOT include 'microcloud' — the system appends that automatically. Default: 'lab'.",
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
                        "ceph_disks_per_node": {
                            "type": "integer",
                            "description": (
                                "Number of Ceph OSD disks per node. Default: 1. "
                                "Recommend 2 for higher storage throughput or larger clusters."
                            ),
                            "default": 1,
                        },
                        "local_disk_gib": {
                            "type": "integer",
                            "description": (
                                "Size in GiB of a local ZFS disk per node. Default: 0 (disabled). "
                                "Set >= 10 to add fast local storage alongside distributed Ceph."
                            ),
                            "default": 0,
                        },
                        "network_interface": {
                            "type": "string",
                            "description": "Optional override for cluster NIC; auto-detected in nested-LXD mode.",
                        },
                        "ovn_uplink_interface": {
                            "type": "string",
                            "description": "Optional override for OVN uplink NIC; auto-detected in nested-LXD mode.",
                        },
                        "ceph_osd_disk": {
                            "type": "string",
                            "description": "Optional bare-metal OSD disk override; nested-LXD mode provisions per-node virtual disks automatically.",
                        },
                    },
                    "required": ["nodes"],
                },
            },
            {
                "name": "delete_environment",
                "description": (
                    "Destroy a deployed MicroCloud environment and clean up all associated resources. "
                    "This runs terraform destroy to remove VMs, networks, and storage volumes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {
                            "type": "string",
                            "description": "Name of the workspace/environment to delete (e.g. lab_microcloud)",
                        },
                    },
                    "required": ["workspace"],
                },
            },
            {
                "name": "list_environments",
                "description": (
                    "List managed MicroCloud environments with node counts and running status. "
                    "Use this before delete or scale operations when workspace name is unknown."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "scale_environment",
                "description": (
                    "Scale an existing MicroCloud environment to a target node count >= 3. "
                    "Use after explicit user confirmation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {
                            "type": "string",
                            "description": "Workspace/environment name to scale (e.g. lab_microcloud)",
                        },
                        "target_nodes": {
                            "type": "integer",
                            "description": "Desired total node count after scaling (>= 3)",
                        },
                        "sizing_tier": {
                            "type": "string",
                            "enum": ["minimal", "small", "medium", "large", "conservative", "performance"],
                        },
                        "node_cpu": {"type": "integer"},
                        "node_memory_mb": {"type": "integer"},
                        "root_disk_gib": {"type": "integer"},
                        "ceph_disk_gib": {"type": "integer"},
                    },
                    "required": ["workspace", "target_nodes"],
                },
            },
            {
                "name": "add_cluster_node",
                "description": (
                    "Add one or more new nodes to an existing MicroCloud cluster. "
                    "Provisions new VMs via OpenTofu, installs snaps, then uses 'microcloud add' "
                    "to expand the live cluster. Use after user confirms the workspace to expand."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {
                            "type": "string",
                            "description": "Workspace/environment name to expand (e.g. lab_microcloud)",
                        },
                        "add_nodes": {
                            "type": "integer",
                            "description": "Number of nodes to add (default: 1)",
                            "default": 1,
                        },
                        "ceph_disk_gib": {"type": "integer"},
                        "ceph_disks_per_node": {
                            "type": "integer",
                            "description": "Number of Ceph OSD disks per new node. Should match existing nodes.",
                            "default": 1,
                        },
                        "local_disk_gib": {
                            "type": "integer",
                            "description": "Local ZFS disk size in GiB for new nodes. 0 = disabled.",
                            "default": 0,
                        },
                        "sizing_tier": {
                            "type": "string",
                            "enum": ["minimal", "small", "medium", "large", "conservative", "performance"],
                        },
                        "node_cpu": {"type": "integer"},
                        "node_memory_mb": {"type": "integer"},
                        "root_disk_gib": {"type": "integer"},
                    },
                    "required": ["workspace"],
                },
            },
            {
                "name": "verify_cluster_health",
                "description": (
                    "Verify the health of all services in a deployed MicroCloud cluster "
                    "(MicroCloud, LXD, MicroCeph, MicroOVN). Use after deployment or when "
                    "the user asks about cluster status or health."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {
                            "type": "string",
                            "description": "Workspace/environment name to check (e.g. lab_microcloud)",
                        },
                    },
                    "required": ["workspace"],
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
