"""Tool definitions for the MicroCloud-first AI agent."""

from typing import Any


def get_tool_definitions() -> dict[str, Any]:
    """Return tool definitions used by the AI assistant."""
    return {
        "tools": [
            {
                "name": "prep_host",
                "description": "Prepare the Ubuntu host for MicroCloud and local inference snaps",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "install_inference": {
                            "type": "boolean",
                            "description": "Install the inference snap as part of host preparation",
                            "default": True,
                        },
                        "install_microcloud_prereqs": {
                            "type": "boolean",
                            "description": "Install common host packages needed for MicroCloud work",
                            "default": True,
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "install_inference_snap",
                "description": "Install the Canonical inference snap used by this assistant",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "engine": {
                            "type": "string",
                            "description": "Inference snap name",
                            "default": "nemotron-3-nano",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "deploy_microcloud",
                "description": "Deploy a MicroCloud cluster with specified nodes and configuration",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nodes": {
                            "type": "integer",
                            "description": "Number of nodes in the cluster",
                            "default": 3,
                        },
                        "network_interface": {
                            "type": "string",
                            "description": "Network interface for cluster communication (e.g., eth0, enp0s3)",
                        },
                        "storage_type": {
                            "type": "string",
                            "enum": ["lvm", "ceph"],
                            "description": "Storage backend type",
                            "default": "lvm",
                        },
                        "storage_size": {
                            "type": "string",
                            "description": "Storage pool size (e.g., 50GB, 100GB)",
                        },
                        "preseed_file": {
                            "type": "string",
                            "description": "Path to custom preseed file (optional)",
                        },
                    },
                    "required": ["nodes", "network_interface"],
                },
            },
            {
                "name": "get_documentation",
                "description": "Fetch relevant MicroCloud documentation for a topic",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Documentation topic (e.g., 'microcloud-setup', 'storage', 'networking')",
                        }
                    },
                    "required": ["topic"],
                },
            },
        ]
    }


def get_tool_by_name(name: str) -> dict[str, Any] | None:
    """Get a tool definition by name."""
    tools = get_tool_definitions()["tools"]
    for tool in tools:
        if tool["name"] == name:
            return tool
    return None


def validate_tool_parameters(tool_name: str, parameters: dict[str, Any]) -> tuple[bool, str]:
    """Validate parameters against the selected tool schema."""
    tool = get_tool_by_name(tool_name)
    if not tool:
        return False, f"Tool '{tool_name}' not found"

    schema = tool["parameters"]
    required_params = schema.get("required", [])

    for param in required_params:
        if param not in parameters:
            return False, f"Missing required parameter: {param}"

    return True, ""
