"""MicroCloud planning primitives used by the AI-driven topology flow."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class StorageBackend(str, Enum):
    CEPH = "ceph"  # MicroCloud always uses Ceph; LVM is not a valid option


class NetworkMode(str, Enum):
    NO_OVN = "no_ovn"
    OVN = "ovn"


class SizingTier(str, Enum):
    MINIMAL = "minimal"  # PoC / dev, absolute minimum resources
    SMALL = "small"  # Small team lab, 3 nodes
    MEDIUM = "medium"  # Production-like staging, 3–5 nodes
    LARGE = "large"  # Full HA production, 5+ nodes


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
        cpu=2,
        ram_gb=4,
        root_disk_gb=20,
        storage_disk_gb=20,
    ),
    SizingTier.SMALL: NodeSizing(
        tier=SizingTier.SMALL,
        cpu=4,
        ram_gb=8,
        root_disk_gb=40,
        storage_disk_gb=50,
    ),
    SizingTier.MEDIUM: NodeSizing(
        tier=SizingTier.MEDIUM,
        cpu=8,
        ram_gb=16,
        root_disk_gb=60,
        storage_disk_gb=100,
    ),
    SizingTier.LARGE: NodeSizing(
        tier=SizingTier.LARGE,
        cpu=16,
        ram_gb=32,
        root_disk_gb=80,
        storage_disk_gb=500,
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


SCENARIOS: dict[str, MCScenario] = {
    "custom": MCScenario(
        name="custom",
        label="AI-Designed Topology",
        description=(
            "No baseline templates. The AI designs topology from scratch based on "
            "workload intent, host limits, reliability target, and cost trade-offs."
        ),
        min_nodes=3,
        default_nodes=3,
        network_mode=NetworkMode.OVN,
        storage_backend=StorageBackend.CEPH,
        default_sizing=SizingTier.SMALL,
        requires_dedicated_storage_disk=True,
        required_params=["nodes"],
        optional_params=[
            "ceph_disks_per_node",
            "local_disk_gib",
        ],
        notes=(
            "This automation currently enables OVN for every deployment. "
            "Ceph OSD disks are always required per node; in nested-LXD mode they are virtual block volumes "
            "provisioned by Terraform rather than host physical disks."
        ),
    )
}


def get_scenario(name: str) -> MCScenario | None:
    """Look up a scenario by name (case-insensitive)."""
    key = (name or "custom").lower()
    return SCENARIOS.get(key) or SCENARIOS.get("custom")


def scenarios_summary() -> str:
    """Return a short summary for the AI prompt."""
    lines = ["MicroCloud planning mode: AI-designed topology (no fixed baselines).\n"]
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
