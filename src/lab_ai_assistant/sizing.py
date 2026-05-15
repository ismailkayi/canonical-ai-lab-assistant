"""
MicroCloud sizing advisor.

Replicates (and extends) the interactive sizing advisor from orchestrate.sh
but in a form the AI agent can call programmatically.
"""

from dataclasses import dataclass
from typing import Any

from lab_ai_assistant.scenarios import (
    MCScenario,
    NodeSizing,
    SizingTier,
    SIZING_TIERS,
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

    def _build_rationale(
        self, tier: SizingTier, scenario: MCScenario, description: str
    ) -> str:
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

    def _build_warnings(
        self, tier: SizingTier, scenario: MCScenario, node_count: int
    ) -> list[str]:
        warnings = []
        sizing = SIZING_TIERS[tier]

        if tier == SizingTier.MINIMAL:
            warnings.append(
                "Minimal sizing may cause "
                "instability. Consider upgrading to the 'small' tier."
            )

        if scenario.storage_backend.value == "ceph" and node_count < 3:
            warnings.append(
                "Ceph requires a minimum of 3 OSD nodes. "
                "Increase the node count to at least 3."
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
