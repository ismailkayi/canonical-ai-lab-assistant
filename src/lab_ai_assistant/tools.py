"""
Tool definitions for the MicroCloud AI agent.

These tools are what the LLM can call. The orchestrator executes them.
"""

import re
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
                        },
                        "model": {
                            "type": "string",
                            "description": (
                                "Optional model variant to select. Smaller models download "
                                "faster and need less RAM (gemma4: e2b ~2.9GB, e4b ~5.0GB "
                                "default, 26b ~15.8GB)."
                            ),
                        },
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
                        "node_count": {"type": "integer", "minimum": 3, "maximum": 50},
                        "node_cpu": {"type": "integer", "minimum": 1},
                        "node_ram_gb": {"type": "integer", "minimum": 1},
                        "root_disk_gb": {"type": "integer", "minimum": 20},
                        "ceph_disk_gb": {"type": "integer", "minimum": 10},
                        "network_mode": {
                            "type": "string",
                            "enum": ["standard-2nic", "fully-segregated-4nic"],
                            "description": (
                                "Proposed network layout. Use fully-segregated-4nic "
                                "when the user requests distinct OVN and Ceph planes."
                            ),
                            "default": "standard-2nic",
                        },
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
                        "nodes": {"type": "integer", "minimum": 3, "maximum": 50},
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
                    "Both phases always execute together. Call this as soon as the requested "
                    "plan is complete; the orchestrator validates it, displays the exact "
                    "resolved plan, and obtains approval before anything executes. Do not ask "
                    "for an informal confirmation first."
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
                            "pattern": "^[A-Za-z0-9][A-Za-z0-9-]{0,31}$",
                            "description": "Short word prefix for resource naming, e.g. 'lab' or 'alice'. Do NOT include 'microcloud' — the system appends that automatically. Default: 'lab'.",
                            "default": "lab",
                        },
                        "nodes": {
                            "type": "integer",
                            "minimum": 3,
                            "maximum": 50,
                            "default": 3,
                        },
                        "sizing_tier": {
                            "type": "string",
                            "enum": [
                                "minimal",
                                "small",
                                "medium",
                                "large",
                                "conservative",
                                "performance",
                            ],
                        },
                        "node_cpu": {"type": "integer", "minimum": 1},
                        "node_memory_mb": {"type": "integer", "minimum": 1024},
                        "root_disk_gib": {"type": "integer", "minimum": 20},
                        "ceph_disk_gib": {"type": "integer", "minimum": 10},
                        "ceph_disks_per_node": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 8,
                            "description": (
                                "Number of Ceph OSD disks per node. Default: 1. "
                                "Recommend 2 for higher storage throughput or larger clusters."
                            ),
                            "default": 1,
                        },
                        "local_disk_gib": {
                            "type": "integer",
                            "minimum": 0,
                            "description": (
                                "Size in GiB of a local ZFS disk per node. Default: 0 (disabled). "
                                "Set >= 10 to add fast local storage alongside distributed Ceph."
                            ),
                            "default": 0,
                        },
                        "network_mode": {
                            "type": "string",
                            "enum": ["standard-2nic", "fully-segregated-4nic"],
                            "description": (
                                "Network plane layout. standard-2nic keeps management/cluster "
                                "traffic together plus an IP-free OVN uplink. "
                                "fully-segregated-4nic adds dedicated static-IP OVN underlay "
                                "and Ceph public/internal planes. Use it when explicitly "
                                "requested or when teaching network segregation."
                            ),
                            "default": "standard-2nic",
                        },
                        "ovn_underlay_cidr": {
                            "type": "string",
                            "pattern": r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$",
                            "description": (
                                "Optional advanced override for the dedicated OVN Geneve "
                                "underlay IPv4 subnet. The system selects a non-overlapping "
                                "/24 when omitted."
                            ),
                        },
                        "ceph_network_cidr": {
                            "type": "string",
                            "pattern": r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$",
                            "description": (
                                "Optional advanced override for the dedicated Ceph public "
                                "and internal IPv4 subnet. The system selects a "
                                "non-overlapping /24 when omitted."
                            ),
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
                            "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
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
                "name": "list_orphaned_projects",
                "description": (
                    "Read-only audit of Canonical AI Lab Assistant LXD projects. "
                    "Reports projects and tagged networks not represented by matching "
                    "Terraform state, plus unowned legacy resources that require manual "
                    "review. Never deletes or adopts resources."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "delete_orphaned_project",
                "description": (
                    "Delete one LXD project previously reported as ORPHAN and its "
                    "owner-tagged global networks. Requires exact project and workspace; "
                    "the script refuses unowned resources and projects still present in "
                    "Terraform state. The orchestrator obtains approval before execution."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project": {
                            "type": "string",
                            "pattern": "^[a-z0-9][a-z0-9-]{0,62}$",
                        },
                        "workspace": {
                            "type": "string",
                            "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
                        },
                    },
                    "required": ["project", "workspace"],
                },
            },
            {
                "name": "scale_environment",
                "description": (
                    "Safely expand an existing MicroCloud environment to a larger target "
                    "node count by delegating to the live add-member workflow. Existing "
                    "node and disk geometry is preserved. Downscale is not supported."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {
                            "type": "string",
                            "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
                            "description": "Workspace/environment name to scale (e.g. lab_microcloud)",
                        },
                        "target_nodes": {
                            "type": "integer",
                            "minimum": 3,
                            "maximum": 50,
                            "description": "Desired larger total node count (3-50)",
                        },
                    },
                    "required": ["workspace", "target_nodes"],
                },
            },
            {
                "name": "add_cluster_node",
                "description": (
                    "Add one or more new nodes to an existing MicroCloud cluster. "
                    "Provisions new VMs via OpenTofu, installs snaps, then uses 'microcloud add' "
                    "to expand the live cluster. New nodes inherit the exact CPU, RAM, disk, "
                    "image, network, and storage-pool geometry stored by the deployment."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {
                            "type": "string",
                            "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
                            "description": "Workspace/environment name to expand (e.g. lab_microcloud)",
                        },
                        "add_nodes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 47,
                            "description": "Number of nodes to add (default: 1)",
                            "default": 1,
                        },
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
                            "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
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
        if isinstance(tool, dict) and tool.get("name") == name:
            return tool
    return None


def validate_tool_parameters(tool_name: str, parameters: dict[str, Any]) -> tuple[bool, str]:
    """Validate required fields, types, enums, ranges, and unknown properties."""
    tool = get_tool_by_name(tool_name)
    if not tool:
        return False, f"Unknown tool: '{tool_name}'"

    if not isinstance(parameters, dict):
        return False, "Tool parameters must be an object"

    schema = tool["parameters"]
    properties = schema.get("properties", {})
    unknown = sorted(set(parameters) - set(properties))
    if unknown:
        return False, f"Unsupported parameter(s): {', '.join(unknown)}"

    required = schema.get("required", [])
    for param in required:
        if param not in parameters:
            return False, f"Missing required parameter: '{param}'"

    for name, value in parameters.items():
        field = properties[name]
        expected_type = field.get("type")
        if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            return False, f"Parameter '{name}' must be an integer"
        if expected_type == "string" and not isinstance(value, str):
            return False, f"Parameter '{name}' must be a string"
        if expected_type == "boolean" and not isinstance(value, bool):
            return False, f"Parameter '{name}' must be a boolean"

        if "enum" in field and value not in field["enum"]:
            allowed = ", ".join(str(item) for item in field["enum"])
            return False, f"Parameter '{name}' must be one of: {allowed}"
        if "minimum" in field and value < field["minimum"]:
            return False, f"Parameter '{name}' must be >= {field['minimum']}"
        if "maximum" in field and value > field["maximum"]:
            return False, f"Parameter '{name}' must be <= {field['maximum']}"
        if "pattern" in field and not re.fullmatch(field["pattern"], value):
            return False, f"Parameter '{name}' has an invalid format"

    local_disk = parameters.get("local_disk_gib")
    if isinstance(local_disk, int) and 0 < local_disk < 10:
        return False, "Parameter 'local_disk_gib' must be 0 or >= 10"

    return True, ""
