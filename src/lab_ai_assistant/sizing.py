"""Workload-driven sizing with exact, shared deployment resource totals."""

from dataclasses import dataclass
from typing import Any

from lab_ai_assistant.planning import CPU_OVERCOMMIT_RATIO, TopologySpec
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
        return self.topology.total_cpu

    def total_ram_gb(self) -> int:
        return self.topology.total_ram_mb // 1024

    def total_storage_gb(self) -> int:
        return self.topology.total_storage_gib

    @property
    def topology(self) -> TopologySpec:
        return TopologySpec(
            nodes=self.total_nodes,
            node_cpu=self.per_node.cpu,
            node_memory_mb=self.per_node.ram_gb * 1024,
            root_disk_gib=self.per_node.root_disk_gb,
            ceph_disk_gib=self.per_node.storage_disk_gb,
            ceph_disks_per_node=1,
        )

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
                "ceph_gb": self.topology.total_ceph_gib,
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
        """Return reference targets, not fixed deployment promises."""
        lines = ["Sizing reference targets (adjusted to live host budgets):\n"]
        for tier, sizing in SIZING_TIERS.items():
            lines.append(f"  {tier.value:10s} — {sizing.summary()}")
        lines.append(
            "\nReferences assume one OSD per node. Automatic Ceph sizing shares a raw target "
            "of at most 100 GiB per node across OSDs unless a dataset or disk size is specified."
        )
        return "\n".join(lines)

    def host_aware_size(
        self,
        host_state: dict[str, Any],
        nodes: int,
        profile: str = "balanced",
        residual_capacity: bool = False,
        *,
        node_cpu: int | None = None,
        node_memory_mb: int | None = None,
        root_disk_gib: int | None = None,
        ceph_disk_gib: int | None = None,
        ceph_disks_per_node: int = 1,
        local_disk_gib: int = 0,
        dataset_size_gib: int | None = None,
    ) -> "HostAwareSizing":
        """Adapt workload targets to the budget without changing explicit values."""
        if not 3 <= nodes <= 50:
            raise ValueError("Sizing requires between 3 and 50 nodes.")
        profile = (profile or "balanced").lower()
        profile_tiers = {
            "minimal": SizingTier.MINIMAL,
            "conservative": SizingTier.MINIMAL,
            "small": SizingTier.SMALL,
            "balanced": SizingTier.SMALL,
            "custom": SizingTier.SMALL,
            "medium": SizingTier.MEDIUM,
            "performance": SizingTier.MEDIUM,
            "large": SizingTier.LARGE,
        }
        if profile not in profile_tiers:
            raise ValueError(f"Unknown sizing profile: {profile}")
        if not 1 <= ceph_disks_per_node <= 8:
            raise ValueError("ceph_disks_per_node must be between 1 and 8.")
        if dataset_size_gib is not None and dataset_size_gib < 1:
            raise ValueError("dataset_size_gib must be at least 1 GiB.")
        target = SIZING_TIERS[profile_tiers[profile]]
        cpu_total = max(int(host_state.get("cpu_cores", 0) or 0), 0)
        ram_mb = max(int(host_state.get("ram_total_mb", 0) or 0), 0)
        storage_gib = max(int(host_state.get("storage_available_gib", 0) or 0), 0)

        if residual_capacity:
            usable_cpu = cpu_total
            usable_mb = ram_mb
            usable_disk = storage_gib
        else:
            usable_cpu = max(
                int(cpu_total * CPU_OVERCOMMIT_RATIO) - int(host_state.get("consumed_cpu", 0) or 0),
                0,
            )
            reserve_mb = max(ram_mb // 5, 4096)
            usable_mb = min(
                max(
                    int(host_state.get("ram_available_mb", ram_mb) or 0) - reserve_mb,
                    0,
                ),
                max(ram_mb - int(host_state.get("consumed_ram_mb", 0) or 0) - reserve_mb, 0),
            )
            usable_disk = max(storage_gib - 20, 0)

        cpu = node_cpu if node_cpu is not None else max(1, min(target.cpu, usable_cpu // nodes))
        memory = (
            node_memory_mb
            if node_memory_mb is not None
            else max(4096, min(target.ram_gb, usable_mb // (nodes * 1024)) * 1024)
        )
        disk_budget = usable_disk // nodes
        minimum_ceph = ceph_disk_gib if ceph_disk_gib is not None else 10
        if dataset_size_gib is not None and ceph_disk_gib is None:
            # Three replicas and 20% free space, rounded up across every OSD.
            divisor = nodes * ceph_disks_per_node * 4
            minimum_ceph = max(10, (dataset_size_gib * 15 + divisor - 1) // divisor)
        root = (
            root_disk_gib
            if root_disk_gib is not None
            else max(
                20,
                min(
                    target.root_disk_gb,
                    disk_budget - local_disk_gib - ceph_disks_per_node * minimum_ceph,
                ),
            )
        )
        if ceph_disk_gib is not None:
            ceph = ceph_disk_gib
        elif dataset_size_gib is not None:
            ceph = minimum_ceph
        else:
            # More virtual OSDs need not imply a larger dataset or host allocation.
            desired_ceph = max(10, min(target.storage_disk_gb, 100) // ceph_disks_per_node)
            ceph = max(
                10,
                min(
                    desired_ceph,
                    (disk_budget - root - local_disk_gib) // ceph_disks_per_node,
                ),
            )
        topology = TopologySpec(
            nodes=nodes,
            node_cpu=cpu,
            node_memory_mb=memory,
            root_disk_gib=root,
            ceph_disk_gib=ceph,
            ceph_disks_per_node=ceph_disks_per_node,
            local_disk_gib=local_disk_gib,
        )
        if dataset_size_gib is not None and topology.ceph_dataset_budget_gib < dataset_size_gib:
            raise ValueError(
                f"Explicit Ceph disks support an estimated {topology.ceph_dataset_budget_gib} "
                f"GiB dataset with headroom, below the requested {dataset_size_gib} GiB."
            )
        return HostAwareSizing(
            profile=profile,
            topology=topology,
            host_cpu=usable_cpu,
            host_ram_mb=usable_mb,
            host_storage_gib=usable_disk,
            dataset_size_gib=dataset_size_gib,
        )

    @staticmethod
    def profile_for(tier: str | None, workload: str = "") -> str:
        explicit = (tier or "").strip().lower()
        profiles = {
            "minimal": "conservative",
            "conservative": "conservative",
            "small": "balanced",
            "balanced": "balanced",
            "medium": "medium",
            "large": "large",
            "performance": "performance",
            "custom": "balanced",
        }
        if explicit:
            if explicit not in profiles:
                raise ValueError(f"Unknown sizing tier: {tier}")
            return profiles[explicit]
        text = workload.lower()
        if any(k in text for k in ("performance", "benchmark", "heavy", "large", "production")):
            return "performance"
        if any(k in text for k in ("minimal", "poc", "proof of concept", "dev", "sandbox")):
            return "conservative"
        if any(k in text for k in ("medium", "staging", "preprod")):
            return "medium"
        return "balanced"


@dataclass
class HostAwareSizing:
    profile: str
    topology: TopologySpec
    host_cpu: int
    host_ram_mb: int
    host_storage_gib: int
    dataset_size_gib: int | None = None

    @property
    def nodes(self) -> int:
        return self.topology.nodes

    @property
    def node_cpu(self) -> int:
        return self.topology.node_cpu

    @property
    def node_memory_mb(self) -> int:
        return self.topology.node_memory_mb

    @property
    def node_ram_gb(self) -> float:
        return self.node_memory_mb / 1024

    @property
    def root_disk_gb(self) -> int:
        return self.topology.root_disk_gib

    @property
    def ceph_disk_gb(self) -> int:
        return self.topology.ceph_disk_gib

    @property
    def host_ram_gb(self) -> float:
        return self.host_ram_mb / 1024

    def total_cpu(self) -> int:
        return self.topology.total_cpu

    def total_ram_gb(self) -> float:
        return self.topology.total_ram_mb / 1024

    def total_ceph_gb(self) -> int:
        return self.topology.total_ceph_gib

    def total_storage_gb(self) -> int:
        return self.topology.total_storage_gib

    def fits_host(self) -> bool:
        return (
            self.total_cpu() <= self.host_cpu
            and self.topology.total_ram_mb <= self.host_ram_mb
            and self.total_storage_gb() <= self.host_storage_gib
        )

    def deployment_parameters(self) -> dict[str, Any]:
        topology = self.topology
        return {
            "nodes": topology.nodes,
            "sizing_tier": self.profile,
            "node_cpu": topology.node_cpu,
            "node_memory_mb": topology.node_memory_mb,
            "root_disk_gib": topology.root_disk_gib,
            "ceph_disk_gib": topology.ceph_disk_gib,
            "ceph_disks_per_node": topology.ceph_disks_per_node,
            "local_disk_gib": topology.local_disk_gib,
        }

    def to_dict(self) -> dict[str, Any]:
        topology = self.topology
        return {
            "nodes": topology.nodes,
            "profile": self.profile,
            "per_node": {
                "cpu": topology.node_cpu,
                "ram_gb": topology.node_memory_mb / 1024,
                "memory_mb": topology.node_memory_mb,
                "root_disk_gb": topology.root_disk_gib,
                "ceph_disk_gb": topology.ceph_disk_gib,
                "ceph_disks_per_node": topology.ceph_disks_per_node,
                "local_disk_gib": topology.local_disk_gib,
            },
            "totals": {
                "cpu": self.total_cpu(),
                "ram_gb": self.total_ram_gb(),
                "ceph_gb": self.total_ceph_gb(),
                "storage_gib": self.total_storage_gb(),
                "root_gib": topology.total_root_gib,
                "local_gib": topology.total_local_gib,
                "estimated_ceph_usable_gib": topology.estimated_ceph_usable_gib,
                "dataset_budget_gib": topology.ceph_dataset_budget_gib,
            },
            "host": {
                "cpu": self.host_cpu,
                "ram_gb": self.host_ram_mb / 1024,
                "storage_gib": self.host_storage_gib,
            },
            "resolved_parameters": self.deployment_parameters(),
            "fits_host": self.fits_host(),
        }

    def summary(self) -> str:
        fit = "fits allocation budget" if self.fits_host() else "EXCEEDS allocation budget"
        dataset = (
            f"\nRequested dataset: {self.dataset_size_gib} GiB"
            if self.dataset_size_gib is not None
            else ""
        )
        return (
            f"Host-aware sizing ({self.profile} intent, adjusted to live capacity)\n"
            f"{self.topology.resource_summary()}\n"
            f"Available allocation budget: {self.host_cpu} vCPU / "
            f"{self.host_ram_mb / 1024:g} GiB RAM / "
            f"{self.host_storage_gib} GiB storage -> {fit}"
            f"{dataset}\n"
            "Virtual OSDs share the host pool; more OSDs do not guarantee more physical IOPS."
        )
