"""
MicroCloud deployment scenario catalog.

Each scenario describes a complete topology: node count, network mode,
storage backend, sizing tier, and the required parameters the AI must
collect before deployment can start.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StorageBackend(str, Enum):
    LVM = "lvm"
    CEPH = "ceph"


class NetworkMode(str, Enum):
    FLAT = "flat"          # Simple, no OVN
    OVN = "ovn"            # OVN-based virtual networking (standard MC)
    OVN_UPLINK = "ovn_uplink"  # OVN with physical uplink (full MicroCloud)


class SizingTier(str, Enum):
    MINIMAL = "minimal"    # PoC / dev, absolute minimum resources
    SMALL = "small"        # Small team lab, 3 nodes
    MEDIUM = "medium"      # Production-like staging, 3–5 nodes
    LARGE = "large"        # Full HA production, 5+ nodes


@dataclass
class NodeSizing:
    tier: SizingTier
    cpu: int
    ram_gb: int
    root_disk_gb: int
    storage_disk_gb: int  # dedicated disk for LVM / Ceph OSD

    def summary(self) -> str:
        return (
            f"{self.tier.value}: "
            f"{self.cpu} vCPU / {self.ram_gb} GB RAM / "
            f"{self.root_disk_gb} GB root / {self.storage_disk_gb} GB storage disk"
        )


# Sizing tiers aligned with orchestrate.sh sizing advisor
SIZING_TIERS: dict[SizingTier, NodeSizing] = {
    SizingTier.MINIMAL: NodeSizing(
        tier=SizingTier.MINIMAL,
        cpu=2, ram_gb=4,
        root_disk_gb=20, storage_disk_gb=20,
    ),
    SizingTier.SMALL: NodeSizing(
        tier=SizingTier.SMALL,
        cpu=4, ram_gb=8,
        root_disk_gb=40, storage_disk_gb=50,
    ),
    SizingTier.MEDIUM: NodeSizing(
        tier=SizingTier.MEDIUM,
        cpu=8, ram_gb=16,
        root_disk_gb=60, storage_disk_gb=100,
    ),
    SizingTier.LARGE: NodeSizing(
        tier=SizingTier.LARGE,
        cpu=16, ram_gb=32,
        root_disk_gb=80, storage_disk_gb=500,
    ),
}


@dataclass
class MCScenario:
    name: str
    label: str
    description: str
    min_nodes: int
    default_nodes: int
    network_mode: NetworkMode
    storage_backend: StorageBackend
    default_sizing: SizingTier
    requires_dedicated_storage_disk: bool
    required_params: list[str]
    optional_params: list[str]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "min_nodes": self.min_nodes,
            "default_nodes": self.default_nodes,
            "network_mode": self.network_mode.value,
            "storage_backend": self.storage_backend.value,
            "default_sizing": self.default_sizing.value,
            "required_params": self.required_params,
            "optional_params": self.optional_params,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Scenario catalog
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, MCScenario] = {

    "minimal": MCScenario(
        name="minimal",
        label="Minimal (PoC / Dev)",
        description=(
            "Smallest possible 3-node MicroCloud, no OVN, no Ceph. "
            "Uses LVM local storage. Ideal for a quick proof-of-concept or "
            "a developer sandbox where resource usage must be minimal."
        ),
        min_nodes=3,
        default_nodes=3,
        network_mode=NetworkMode.FLAT,
        storage_backend=StorageBackend.LVM,
        default_sizing=SizingTier.MINIMAL,
        requires_dedicated_storage_disk=True,
        required_params=["network_interface", "storage_disk"],
        optional_params=["nodes", "storage_size"],
        notes="OVN is disabled. No distributed Ceph storage. Suitable for lab only.",
    ),

    "standard": MCScenario(
        name="standard",
        label="Standard (3-node with OVN + LVM)",
        description=(
            "Standard 3-node MicroCloud with MicroOVN for virtual networking "
            "and LVM for local storage. The baseline scenario for most lab "
            "deployments."
        ),
        min_nodes=3,
        default_nodes=3,
        network_mode=NetworkMode.OVN,
        storage_backend=StorageBackend.LVM,
        default_sizing=SizingTier.SMALL,
        requires_dedicated_storage_disk=True,
        required_params=["network_interface", "ovn_uplink_interface", "storage_disk"],
        optional_params=["nodes", "storage_size", "ipv4_gateway", "ipv4_range"],
        notes="Requires a dedicated NIC or VLAN for OVN uplink traffic.",
    ),

    "ha": MCScenario(
        name="ha",
        label="High Availability (5-node, Ceph + OVN)",
        description=(
            "Full 5-node HA MicroCloud: MicroCeph for distributed block storage, "
            "MicroOVN for virtual networking, LXD cluster for compute. "
            "Suitable for staging/production environments."
        ),
        min_nodes=5,
        default_nodes=5,
        network_mode=NetworkMode.OVN_UPLINK,
        storage_backend=StorageBackend.CEPH,
        default_sizing=SizingTier.MEDIUM,
        requires_dedicated_storage_disk=True,
        required_params=[
            "network_interface",
            "ovn_uplink_interface",
            "storage_disk",
            "ceph_osd_disk",
        ],
        optional_params=[
            "nodes",
            "storage_size",
            "ipv4_gateway",
            "ipv4_range",
            "ipv6_gateway",
            "ipv6_range",
        ],
        notes=(
            "Ceph requires at least 3 OSD disks across the cluster. "
            "Plan ≥ 1 dedicated disk per node for Ceph OSD."
        ),
    ),

    "custom": MCScenario(
        name="custom",
        label="Custom (user-defined)",
        description=(
            "Fully custom topology. The AI collects all parameters from the user "
            "based on their specific requirements."
        ),
        min_nodes=3,
        default_nodes=3,
        network_mode=NetworkMode.OVN,
        storage_backend=StorageBackend.LVM,
        default_sizing=SizingTier.SMALL,
        requires_dedicated_storage_disk=True,
        required_params=["network_interface", "storage_disk"],
        optional_params=[
            "nodes",
            "storage_size",
            "ovn_uplink_interface",
            "ceph_osd_disk",
            "ipv4_gateway",
            "ipv4_range",
            "ipv6_gateway",
            "ipv6_range",
            "preseed_file",
        ],
        notes="All options are negotiated during the conversation.",
    ),
}


def get_scenario(name: str) -> MCScenario | None:
    """Look up a scenario by name (case-insensitive)."""
    return SCENARIOS.get(name.lower())


def scenarios_summary() -> str:
    """Return a human-readable catalog for use in the AI system prompt."""
    lines = ["Available MicroCloud deployment scenarios:\n"]
    for sc in SCENARIOS.values():
        sizing = SIZING_TIERS[sc.default_sizing]
        lines.append(
            f"  [{sc.name}]  {sc.label}\n"
            f"    {sc.description}\n"
            f"    Nodes: {sc.default_nodes} (min {sc.min_nodes}) | "
            f"Network: {sc.network_mode.value} | "
            f"Storage: {sc.storage_backend.value}\n"
            f"    Default sizing: {sizing.summary()}\n"
            f"    Required params: {', '.join(sc.required_params)}\n"
            f"    Notes: {sc.notes}\n"
        )
    return "\n".join(lines)
