"""Tool definitions for AI agent function calling."""

from typing import Any


def get_tool_definitions() -> dict[str, Any]:
    """
    Return standardized tool definitions for AI function calling.
    
    Returns:
        Tool schema compatible with function calling APIs.
    """
    return {
        "tools": [
            {
                "name": "deploy_microcloud",
                "description": "Deploy a MicroCloud cluster with specified nodes and configuration",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nodes": {
                            "type": "integer",
                            "description": "Number of nodes in the cluster (minimum 3)",
                            "default": 3
                        },
                        "network_interface": {
                            "type": "string",
                            "description": "Network interface for cluster communication (e.g., eth0, enp0s3)",
                        },
                        "storage_type": {
                            "type": "string",
                            "enum": ["lvm", "ceph"],
                            "description": "Storage backend type",
                            "default": "lvm"
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
                    "required": ["nodes", "network_interface"]
                }
            },
            {
                "name": "deploy_k8s_snap",
                "description": "Deploy Canonical Kubernetes using snap on provided nodes",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "control_plane_nodes": {
                            "type": "integer",
                            "description": "Number of control plane nodes",
                            "default": 1
                        },
                        "worker_nodes": {
                            "type": "integer",
                            "description": "Number of worker nodes",
                            "default": 1
                        },
                        "network_interface": {
                            "type": "string",
                            "description": "Network interface for cluster",
                        },
                    },
                    "required": ["control_plane_nodes", "worker_nodes", "network_interface"]
                }
            },
            {
                "name": "deploy_k8s_juju",
                "description": "Deploy Canonical Kubernetes using Juju with controller and machines",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "control_plane_nodes": {
                            "type": "integer",
                            "description": "Number of control plane nodes (minimum 1, 3+ for HA)",
                            "default": 1
                        },
                        "worker_nodes": {
                            "type": "integer",
                            "description": "Number of worker nodes",
                            "default": 1
                        },
                        "network_interface": {
                            "type": "string",
                            "description": "Network interface for cluster communication",
                        },
                    },
                    "required": ["control_plane_nodes", "worker_nodes", "network_interface"]
                }
            },
            {
                "name": "manage_lab",
                "description": "Manage existing lab deployment (update, rebuild, or delete)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "string",
                            "enum": ["microcloud", "k8s-snap", "k8s-juju"],
                            "description": "Type of deployment to manage"
                        },
                        "action": {
                            "type": "string",
                            "enum": ["update", "rebuild", "delete"],
                            "description": "Management action to perform"
                        },
                        "workspace": {
                            "type": "string",
                            "description": "Workspace identifier (optional, auto-detect if single workspace)",
                        },
                    },
                    "required": ["scenario", "action"]
                }
            },
            {
                "name": "get_lab_status",
                "description": "Get current status of lab deployment",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "string",
                            "enum": ["microcloud", "k8s-snap", "k8s-juju"],
                            "description": "Type of deployment to query"
                        },
                        "workspace": {
                            "type": "string",
                            "description": "Workspace identifier (optional)",
                        },
                    },
                    "required": ["scenario"]
                }
            },
            {
                "name": "list_workspaces",
                "description": "List all available deployments/workspaces",
                "parameters": {
                    "type": "object",
                    "properties": {},
                }
            },
            {
                "name": "get_documentation",
                "description": "Fetch relevant documentation for a topic",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Documentation topic (e.g., 'microcloud-setup', 'k8s-requirements')",
                        },
                    },
                    "required": ["topic"]
                }
            }
        ]
    }


def get_tool_by_name(name: str) -> dict[str, Any] | None:
    """Get specific tool definition by name."""
    tools = get_tool_definitions()["tools"]
    for tool in tools:
        if tool["name"] == name:
            return tool
    return None


def validate_tool_parameters(tool_name: str, parameters: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate parameters against tool schema.
    
    Args:
        tool_name: Name of the tool
        parameters: Parameters to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    tool = get_tool_by_name(tool_name)
    if not tool:
        return False, f"Tool '{tool_name}' not found"
    
    schema = tool["parameters"]
    required_params = schema.get("required", [])
    
    # Check required parameters
    for param in required_params:
        if param not in parameters:
            return False, f"Missing required parameter: {param}"
    
    # TODO: Add type validation here
    return True, ""
