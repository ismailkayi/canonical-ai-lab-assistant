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
    CEPH = "ceph"  # MicroCloud always uses Ceph; LVM is not a valid option


class NetworkMode(str, Enum):
    NO_OVN = "no_ovn"      # Ceph only, no OVN — explicit opt-out, unusual
    OVN = "ovn"            # Standard MicroCloud with MicroOVN (default)


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

    # ------------------------------------------------------------------
    # STANDARD: the default for almost every deployment
    # 3-node, MicroCeph for distributed storage, MicroOVN for networking
    # ------------------------------------------------------------------
    "standard": MCScenario(
        name="standard",
        label="Standard (3-node, OVN + Ceph)",
        description=(
            "The baseline MicroCloud: 3 nodes, MicroCeph for distributed block "
            "storage, MicroOVN for virtual networking. Each node needs one "
            "dedicated disk for Ceph OSD. Suitable for most lab environments."
        ),
        min_nodes=3,
        default_nodes=3,
        network_mode=NetworkMode.OVN,
        storage_backend=StorageBackend.CEPH,
        default_sizing=SizingTier.SMALL,
        requires_dedicated_storage_disk=True,
        required_params=["network_interface", "ovn_uplink_interface", "ceph_osd_disk"],
        optional_params=["nodes", "ipv4_gateway", "ipv4_range"],
        notes=(
            "The dedicated OVN uplink NIC must have no IP address assigned. "
            "The Ceph OSD disk must be unformatted and not mounted."
        ),
    ),

    # ------------------------------------------------------------------
    # HA: 5-node, full resilience
    # ------------------------------------------------------------------
    "ha": MCScenario(
        name="ha",
        label="High Availability (5-node, OVN + Ceph)",
        description=(
            "Production-grade 5-node MicroCloud: MicroCeph (5 OSDs) for "
            "redundant distributed storage, MicroOVN for virtual networking. "
            "Can tolerate the loss of any 2 nodes without data loss."
        ),
        min_nodes=5,
        default_nodes=5,
        network_mode=NetworkMode.OVN,
        storage_backend=StorageBackend.CEPH,
        default_sizing=SizingTier.MEDIUM,
        requires_dedicated_storage_disk=True,
        required_params=["network_interface", "ovn_uplink_interface", "ceph_osd_disk"],
        optional_params=["nodes", "ipv4_gateway", "ipv4_range", "ipv6_gateway", "ipv6_range"],
        notes=(
            "5 nodes = Ceph replication factor 3 with 2 spare. "
            "Each node needs a dedicated, unformatted disk for Ceph OSD."
        ),
    ),

    # ------------------------------------------------------------------
    # NO_OVN: only when the user explicitly requests it
    # ------------------------------------------------------------------
    "no_ovn": MCScenario(
        name="no_ovn",
        label="Ceph-only (no OVN)",
        description=(
            "3-node MicroCloud with MicroCeph but WITHOUT MicroOVN. "
            "Use this only when the user explicitly says they don't need "
            "overlay networking. Flat networking via the cluster bridge only."
        ),
        min_nodes=3,
        default_nodes=3,
        network_mode=NetworkMode.NO_OVN,
        storage_backend=StorageBackend.CEPH,
        default_sizing=SizingTier.SMALL,
        requires_dedicated_storage_disk=True,
        required_params=["network_interface", "ceph_osd_disk"],
        optional_params=["nodes"],
        notes=(
            "No OVN means no tenant networking isolation. Instances share the "
            "cluster bridge. Only choose this if the user explicitly opts out of OVN."
        ),
    ),

    # ------------------------------------------------------------------
    # CUSTOM: AI-designed topology, no predefined constraints
    # ------------------------------------------------------------------
    "custom": MCScenario(
        name="custom",
        label="Custom (AI-designed topology)",
        description=(
            "The AI reasons from scratch about the optimal deployment. "
            "Not bound to standard node counts. The AI decides everything "
            "based on the user's workload, team size, budget, and HA needs."
        ),
        min_nodes=3,
        default_nodes=3,
        network_mode=NetworkMode.OVN,
        storage_backend=StorageBackend.CEPH,
        default_sizing=SizingTier.SMALL,
        requires_dedicated_storage_disk=True,
        required_params=["network_interface", "ovn_uplink_interface", "ceph_osd_disk"],
        optional_params=[
            "nodes",
            "ipv4_gateway",
            "ipv4_range",
            "ipv6_gateway",
            "ipv6_range",
            "preseed_file",
        ],
        notes="All topology decisions are made by the AI based on the conversation.",
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
