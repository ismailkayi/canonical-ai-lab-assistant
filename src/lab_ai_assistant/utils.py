"""Utility functions for Lab AI Assistant."""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_json_file(file_path: Path) -> dict[str, Any] | None:
    """Load JSON from file."""
    try:
        with open(file_path) as f:
            value = json.load(f)
            return value if isinstance(value, dict) else None
    except Exception as e:
        logger.error(f"Error loading JSON from {file_path}: {e}")
        return None


def save_json_file(file_path: Path, data: dict[str, Any]) -> bool:
    """Save JSON to file."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving JSON to {file_path}: {e}")
        return False


def parse_parameters_from_text(text: str) -> dict[str, Any]:
    """
    Extract parameters from natural language text.

    Examples:
        "3 nodes with eth0 network and lvm storage"
        -> {"nodes": 3, "network_interface": "eth0", "storage_type": "lvm"}
    """
    params: dict[str, Any] = {}
    text_lower = text.lower()

    # Number extraction patterns
    if "node" in text_lower:
        import re

        match = re.search(r"(\d+)\s*node", text_lower)
        if match:
            params["nodes"] = int(match.group(1))

    # Network interface patterns
    for interface in ["eth0", "eth1", "enp0s3", "enp0s4", "wlan0"]:
        if interface in text_lower:
            params["network_interface"] = interface
            break

    # Storage type patterns
    if "lvm" in text_lower:
        params["storage_type"] = "lvm"
    elif "ceph" in text_lower:
        params["storage_type"] = "ceph"

    # Storage size patterns
    import re

    size_match = re.search(r"(\d+)\s*(gb|tb|mb)", text_lower)
    if size_match:
        params["storage_size"] = f"{size_match.group(1)}{size_match.group(2).upper()}"

    return params


def format_output(data: Any, format_type: str = "text") -> str:
    """Format output for display."""
    if format_type == "json":
        return json.dumps(data, indent=2)
    elif format_type == "yaml":
        # TODO: Implement YAML formatting
        return str(data)
    else:
        # Plain text
        return str(data)
