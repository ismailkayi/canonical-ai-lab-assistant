"""
MicroCloud sizing advisor.

Replicates (and extends) the interactive sizing advisor from orchestrate.sh
but in a form the AI agent can call programmatically.
"""

from dataclasses import dataclass
from typing import Any

from lab_ai_assistant.scenarios import (
    SIZING_TIERS,
    MCScenario,
    NodeSizing,
    SizingTier,
    get_scenario,
)

# ---------------------------------------------------------------------------
# Workload profiles → sizing tier mapping
# ---------------------------------------------------------------------------

_WORKLOAD_KEYWORDS: dict[str, SizingTier] = {
    # Minimal / PoC
    "poc": SizingTier.MINIMAL,
    "proof of concept": SizingTier.MINIMAL,
    "test": SizingTier.MINIMAL,
    "dev": SizingTier.MINIMAL,
    "development": SizingTier.MINIMAL,
    "minimal": SizingTier.MINIMAL,
    "small": SizingTier.SMALL,
    "lab": SizingTier.SMALL,
    # Medium / staging
    "staging": SizingTier.MEDIUM,
    "medium": SizingTier.MEDIUM,
    "pre-production": SizingTier.MEDIUM,
    "preprod": SizingTier.MEDIUM,
    # Large / production
    "production": SizingTier.LARGE,
    "prod": SizingTier.LARGE,
    "large": SizingTier.LARGE,
    "enterprise": SizingTier.LARGE,
    "ha": SizingTier.LARGE,
    "high availability": SizingTier.LARGE,
}


@dataclass
class SizingRecommendation:
    tier: SizingTier
    per_node: NodeSizing
    total_nodes: int
    scenario_name: str
    rationale: str
    warnings: list[str]

    def total_cpu(self) -> int:
        return self.per_node.cpu * self.total_nodes

    def total_ram_gb(self) -> int:
        return self.per_node.ram_gb * self.total_nodes

    def total_storage_gb(self) -> int:
        return self.per_node.storage_disk_gb * self.total_nodes

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "scenario": self.scenario_name,
            "nodes": self.total_nodes,
            "per_node": {
                "cpu": self.per_node.cpu,
                "ram_gb": self.per_node.ram_gb,
                "root_disk_gb": self.per_node.root_disk_gb,
                "storage_disk_gb": self.per_node.storage_disk_gb,
            },
            "totals": {
                "cpu": self.total_cpu(),
                "ram_gb": self.total_ram_gb(),
                "storage_gb": self.total_storage_gb(),
            },
            "rationale": self.rationale,
            "warnings": self.warnings,
        }

    def summary(self) -> str:
        w = ""
        if self.warnings:
            w = "\n  ⚠  " + "\n  ⚠  ".join(self.warnings)
        return (
            f"Sizing recommendation — {self.tier.value.upper()} tier\n"
            f"  Scenario : {self.scenario_name}\n"
            f"  Nodes    : {self.total_nodes}\n"
            f"  Per node : {self.per_node.cpu} vCPU / "
            f"{self.per_node.ram_gb} GB RAM / "
            f"{self.per_node.root_disk_gb} GB root / "
            f"{self.per_node.storage_disk_gb} GB storage\n"
            f"  Totals   : {self.total_cpu()} vCPU / "
            f"{self.total_ram_gb()} GB RAM / "
            f"{self.total_storage_gb()} GB storage\n"
            f"  Rationale: {self.rationale}"
            f"{w}"
        )


class SizingAdvisor:
    """
    Recommend MicroCloud sizing based on user intent.

    The advisor can be called by the AI agent to answer questions such as:
    - "How much disk do I need for a 5-node HA cluster?"
    - "What sizing tier fits a staging environment for 10 developers?"
    """

    def recommend(
        self,
        scenario_name: str,
        nodes: int | None = None,
        workload_description: str = "",
        override_tier: SizingTier | None = None,
    ) -> SizingRecommendation:
        """
        Produce a sizing recommendation.

        Args:
            scenario_name: topology class (custom is the only supported value)
            nodes: explicit node count (overrides scenario default)
            workload_description: free-text description used to auto-select tier
            override_tier: force a specific tier

        Returns:
            SizingRecommendation with per-node and total resource figures
        """
        scenario = get_scenario(scenario_name)
        if scenario is None:
            scenario = get_scenario("custom")
            assert scenario is not None

        node_count = nodes or scenario.default_nodes
        node_count = max(node_count, scenario.min_nodes)

        # Determine tier
        tier = override_tier or self._infer_tier(workload_description, scenario)
        sizing = SIZING_TIERS[tier]

        rationale = self._build_rationale(tier, scenario, workload_description)
        warnings = self._build_warnings(tier, scenario, node_count)

        return SizingRecommendation(
            tier=tier,
            per_node=sizing,
            total_nodes=node_count,
            scenario_name=scenario.name,
            rationale=rationale,
            warnings=warnings,
        )

    def _infer_tier(self, description: str, scenario: MCScenario) -> SizingTier:
        desc_lower = description.lower()
        for keyword, tier in _WORKLOAD_KEYWORDS.items():
            if keyword in desc_lower:
                return tier
        # Fall back to scenario default
        return scenario.default_sizing

    def _build_rationale(self, tier: SizingTier, scenario: MCScenario, description: str) -> str:
        reasons = {
            SizingTier.MINIMAL: (
                "Minimal resources selected for a proof-of-concept or developer "
                "sandbox. Not suitable for production workloads."
            ),
            SizingTier.SMALL: (
                "Small tier selected for a lightweight lab environment. "
                "Suitable for up to a small development team."
            ),
            SizingTier.MEDIUM: (
                "Medium tier selected to support staging or pre-production "
                "workloads with realistic resource pressure."
            ),
            SizingTier.LARGE: (
                "Large tier selected to match production or high-availability "
                "requirements with adequate headroom."
            ),
        }
        base = reasons[tier]
        if description:
            base += f" (Inferred from: '{description[:80]}')"
        if scenario.storage_backend.value == "ceph":
            base += (
                " Note: Ceph distributed storage requires the storage disk "
                "to be a dedicated, unformatted block device on each node."
            )
        return base

    def _build_warnings(self, tier: SizingTier, scenario: MCScenario, node_count: int) -> list[str]:
        warnings = []
        sizing = SIZING_TIERS[tier]

        if tier == SizingTier.MINIMAL:
            warnings.append(
                "Minimal sizing may cause instability. Consider upgrading to the 'small' tier."
            )

        if scenario.storage_backend.value == "ceph" and node_count < 3:
            warnings.append(
                "Ceph requires a minimum of 3 OSD nodes. Increase the node count to at least 3."
            )

        total_storage = sizing.storage_disk_gb * node_count
        if scenario.storage_backend.value == "ceph" and total_storage < 150:
            warnings.append(
                f"Total Ceph storage ({total_storage} GB) is low. "
                "Consider increasing per-node storage disk size."
            )

        return warnings

    def describe_tiers(self) -> str:
        """Return a human-readable table of all sizing tiers."""
        lines = ["Sizing tiers:\n"]
        for tier, sizing in SIZING_TIERS.items():
            lines.append(f"  {tier.value:10s} — {sizing.summary()}")
        return "\n".join(lines)

    def host_aware_size(
        self,
        host_state: dict[str, Any],
        nodes: int,
        profile: str = "balanced",
        residual_capacity: bool = False,
    ) -> "HostAwareSizing":
        """Compute per-node resources from REAL host capacity.

        This mirrors the auto-sizing algorithm in deploy_microcloud.sh exactly,
        so the topology the AI proposes equals what the deploy script provisions.
        Removing this split-brain is the whole point: the AI plans against truth.
        """
        nodes = max(int(nodes or 3), 3)
        cpu_floor = 0 if residual_capacity else 1
        ram_floor = 0 if residual_capacity else 1024
        cpu_total = max(int(host_state.get("cpu_cores", 0) or 0), cpu_floor)
        ram_mb = max(int(host_state.get("ram_total_mb", 0) or 0), ram_floor)
        storage_gib = max(int(host_state.get("storage_available_gib", 0) or 0), 0)
        host_ram_gb = (ram_mb + 1023) // 1024

        if residual_capacity:
            usable_cpu = cpu_total
            usable_mb = ram_mb
            usable_disk = storage_gib
        else:
            reserve_cpu = max(cpu_total // 5, 2)
            usable_cpu = max(cpu_total - reserve_cpu, nodes)

            reserve_mb = max(ram_mb // 5, 4096)
            usable_mb = max(ram_mb - reserve_mb, nodes * 4096)
            usable_disk = max(storage_gib - 20, 120)
        usable_ram_gb = usable_mb // 1024

        ram_tiers = [8, 12, 16, 24, 32, 48, 64, 96, 128]
        ram_tiers_low = [4, 8, 12, 16, 24, 32, 48, 64, 96, 128]
        ceph_tiers = [20, 50, 100, 150, 200, 250, 300, 400, 500]

        bal_cpu = _round_down_even(usable_cpu // nodes, 2)
        bal_ram = _pick_floor_tier(usable_ram_gb // nodes, ram_tiers)
        raw_ceph = max((usable_disk // nodes) - 40, 20)
        bal_ceph = _pick_floor_tier(raw_ceph, ceph_tiers)

        profile = (profile or "balanced").lower()
        if profile in ("minimal", "conservative"):
            node_cpu = _round_down_even(bal_cpu - 2, 2)
            node_ram_gb = _pick_previous_tier(bal_ram, ram_tiers_low)
            root_disk_gb = 30
            ceph_disk_gb = _pick_previous_tier(bal_ceph, ceph_tiers)
        elif profile == "performance":
            node_cpu = min(bal_cpu + 2, max(usable_cpu // nodes, 1))
            ram_limit = _pick_floor_tier(host_ram_gb // nodes, ram_tiers)
            node_ram_gb = _pick_next_tier(bal_ram, ram_limit, ram_tiers)
            root_disk_gb = 50
            ceph_limit = _pick_floor_tier((storage_gib // nodes) - 50, ceph_tiers)
            ceph_disk_gb = _pick_next_tier(bal_ceph, ceph_limit, ceph_tiers)
        else:  # balanced / small / medium / large
            node_cpu = bal_cpu
            node_ram_gb = bal_ram
            root_disk_gb = 40
            ceph_disk_gb = bal_ceph

        node_cpu = max(node_cpu, 1)
        node_ram_gb = max(node_ram_gb, 1)
        root_disk_gb = max(root_disk_gb, 20)
        ceph_disk_gb = max(ceph_disk_gb, 10)

        return HostAwareSizing(
            nodes=nodes,
            profile=profile,
            node_cpu=node_cpu,
            node_ram_gb=node_ram_gb,
            node_memory_mb=node_ram_gb * 1024,
            root_disk_gb=root_disk_gb,
            ceph_disk_gb=ceph_disk_gb,
            host_cpu=cpu_total,
            host_ram_gb=host_ram_gb,
            host_storage_gib=storage_gib,
        )


# ---------------------------------------------------------------------------
# Host-aware auto-sizing (ported 1:1 from deploy_microcloud.sh)
# ---------------------------------------------------------------------------


def _round_down_even(value: int, minimum: int = 2) -> int:
    if value < minimum:
        return minimum
    if value % 2 != 0:
        value -= 1
    return value


def _pick_floor_tier(limit: int, tiers: list[int]) -> int:
    selected = tiers[0]
    for tier in tiers:
        if tier <= limit:
            selected = tier
        else:
            break
    return selected


def _pick_previous_tier(current: int, tiers: list[int]) -> int:
    previous = tiers[0]
    for tier in tiers:
        if tier >= current:
            break
        previous = tier
    return previous


def _pick_next_tier(current: int, limit: int, tiers: list[int]) -> int:
    for tier in tiers:
        if current < tier <= limit:
            return tier
    return current


@dataclass
class HostAwareSizing:
    nodes: int
    profile: str
    node_cpu: int
    node_ram_gb: int
    node_memory_mb: int
    root_disk_gb: int
    ceph_disk_gb: int
    host_cpu: int
    host_ram_gb: int
    host_storage_gib: int

    def total_cpu(self) -> int:
        return self.node_cpu * self.nodes

    def total_ram_gb(self) -> int:
        return self.node_ram_gb * self.nodes

    def total_ceph_gb(self) -> int:
        return self.ceph_disk_gb * self.nodes

    def total_storage_gb(self) -> int:
        return (self.root_disk_gb + self.ceph_disk_gb) * self.nodes

    def fits_host(self) -> bool:
        return (
            self.total_cpu() <= self.host_cpu
            and self.total_ram_gb() <= self.host_ram_gb
            and self.total_storage_gb() <= self.host_storage_gib
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "profile": self.profile,
            "per_node": {
                "cpu": self.node_cpu,
                "ram_gb": self.node_ram_gb,
                "root_disk_gb": self.root_disk_gb,
                "ceph_disk_gb": self.ceph_disk_gb,
            },
            "totals": {
                "cpu": self.total_cpu(),
                "ram_gb": self.total_ram_gb(),
                "ceph_gb": self.total_ceph_gb(),
                "storage_gib": self.total_storage_gb(),
            },
            "host": {
                "cpu": self.host_cpu,
                "ram_gb": self.host_ram_gb,
                "storage_gib": self.host_storage_gib,
            },
            "fits_host": self.fits_host(),
        }

    def summary(self) -> str:
        fit = "fits host capacity" if self.fits_host() else "EXCEEDS host capacity"
        return (
            f"Host-aware sizing ({self.profile} profile) — matches what deploy provisions\n"
            f"  Nodes    : {self.nodes}\n"
            f"  Per node : {self.node_cpu} vCPU / {self.node_ram_gb} GB RAM / "
            f"{self.root_disk_gb} GB root / {self.ceph_disk_gb} GB Ceph disk\n"
            f"  Totals   : {self.total_cpu()} vCPU / {self.total_ram_gb()} GB RAM / "
            f"{self.total_storage_gb()} GB storage ({self.total_ceph_gb()} GB Ceph)\n"
            f"  Available: {self.host_cpu} vCPU / {self.host_ram_gb} GB RAM / "
            f"{self.host_storage_gib} GiB free storage  ->  {fit}"
        )
