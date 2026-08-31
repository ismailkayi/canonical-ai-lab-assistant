"""Typed planning, validation, and approval primitives for infrastructure actions."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MUTATING_ACTIONS = frozenset(
    {
        "prep_host",
        "install_inference_snap",
        "deploy_microcloud",
        "delete_environment",
        "scale_environment",
        "add_cluster_node",
    }
)

AFFIRMATIVE_RESPONSES = frozenset({"yes", "y", "confirm", "proceed", "evet", "e"})
NEGATIVE_RESPONSES = frozenset({"no", "n", "cancel", "abort", "hayır", "hayir", "iptal"})

STANDARD_NETWORK_MODE = "standard-2nic"
SEGREGATED_NETWORK_MODE = "fully-segregated-4nic"


class CapacitySnapshot(BaseModel):
    """Residual host capacity available to a new or expanded lab."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_available: int = Field(ge=0)
    ram_available_mb: int = Field(ge=0)
    storage_available_gib: int = Field(ge=0)
    storage_pool: str = "default"
    source: str = "live-host-observation"


class EnvironmentSnapshot(BaseModel):
    """Exact Terraform state identity and node transition bound to an approval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace: str
    state_lineage: str
    state_serial: int = Field(ge=0)
    current_nodes: int = Field(ge=0, le=50)
    target_nodes: int = Field(ge=0, le=50)
    storage_pool: str


class TopologySpec(BaseModel):
    """Fully resolved deployment geometry used for validation and execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    nodes: int = Field(ge=1, le=50)
    node_cpu: int = Field(ge=1)
    node_memory_mb: int = Field(ge=1024)
    root_disk_gib: int = Field(ge=20)
    ceph_disk_gib: int = Field(ge=10)
    ceph_disks_per_node: int = Field(ge=1, le=8)
    local_disk_gib: int = Field(default=0, ge=0)
    network_mode: Literal["standard-2nic", "fully-segregated-4nic"] = STANDARD_NETWORK_MODE
    ovn_underlay_cidr: str | None = None
    ceph_network_cidr: str | None = None

    @model_validator(mode="after")
    def validate_topology(self) -> "TopologySpec":
        if 0 < self.local_disk_gib < 10:
            raise ValueError("local_disk_gib must be 0 (disabled) or at least 10 GiB")

        cidrs = (self.ovn_underlay_cidr, self.ceph_network_cidr)
        if self.network_mode == STANDARD_NETWORK_MODE:
            if any(cidrs):
                raise ValueError("Dedicated plane CIDRs require fully-segregated-4nic mode")
            return self

        if not all(cidrs):
            raise ValueError(
                "fully-segregated-4nic mode requires ovn_underlay_cidr and ceph_network_cidr"
            )

        networks: list[ipaddress.IPv4Network] = []
        for field_name, value in (
            ("ovn_underlay_cidr", self.ovn_underlay_cidr),
            ("ceph_network_cidr", self.ceph_network_cidr),
        ):
            try:
                network = ipaddress.ip_network(value, strict=True)
            except ValueError as exc:
                raise ValueError(f"{field_name} must be a valid network CIDR: {exc}") from exc
            if not isinstance(network, ipaddress.IPv4Network):
                raise ValueError(f"{field_name} must be an IPv4 network")
            last_node_offset = self.nodes + 9
            if last_node_offset >= network.num_addresses - 1:
                raise ValueError(
                    f"{field_name} does not have usable addresses 10-{last_node_offset} "
                    f"for {self.nodes} nodes"
                )
            networks.append(network)

        if networks[0].overlaps(networks[1]):
            raise ValueError("OVN underlay and Ceph network CIDRs must not overlap")
        return self

    @property
    def total_cpu(self) -> int:
        return self.nodes * self.node_cpu

    @property
    def total_ram_mb(self) -> int:
        return self.nodes * self.node_memory_mb

    @property
    def total_storage_gib(self) -> int:
        per_node = (
            self.root_disk_gib + self.ceph_disks_per_node * self.ceph_disk_gib + self.local_disk_gib
        )
        return self.nodes * per_node


class PlanValidation(BaseModel):
    """Deterministic validation result returned to the planner or user."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ExecutionPlan(BaseModel):
    """Canonical action plan that is validated, displayed, and approved as one unit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    action: str
    parameters: dict[str, Any]
    summary: str
    topology: TopologySpec | None = None
    capacity: CapacitySnapshot | None = None
    environment: EnvironmentSnapshot | None = None
    warnings: tuple[str, ...] = ()

    @property
    def requires_confirmation(self) -> bool:
        return self.action in MUTATING_ACTIONS

    @property
    def digest(self) -> str:
        canonical = self.model_dump_json(
            exclude={"summary", "warnings"},
            exclude_none=True,
        )
        normalized = json.dumps(json.loads(canonical), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @property
    def short_id(self) -> str:
        return self.digest[:12]


class PlanValidator:
    """Validate AI-authored plans against deterministic policy and live capacity."""

    def validate(self, plan: ExecutionPlan) -> PlanValidation:
        errors: list[str] = []
        warnings: list[str] = list(plan.warnings)

        if plan.action == "deploy_microcloud":
            if plan.topology is None:
                errors.append("Deployment plan is missing a fully resolved topology.")
            if plan.capacity is None:
                errors.append("Deployment plan is missing a live capacity snapshot.")
            if plan.topology is not None and plan.capacity is not None:
                if plan.topology.nodes < 3:
                    errors.append("Deployment topology must contain at least 3 nodes.")
                self._validate_capacity(plan.topology, plan.capacity, errors)

        if plan.action == "add_cluster_node":
            add_nodes = self._as_int(plan.parameters.get("add_nodes"), default=1)
            if add_nodes <= 0:
                errors.append("add_nodes must be greater than zero.")

        if plan.action in {"add_cluster_node", "scale_environment"}:
            if plan.environment is None:
                errors.append("Expansion plan is missing an exact environment state snapshot.")
            if plan.topology is None:
                errors.append("Expansion plan is missing its resolved resource delta.")
            if plan.capacity is None:
                errors.append("Expansion plan is missing a live capacity snapshot.")
            if plan.topology is not None and plan.capacity is not None:
                self._validate_capacity(plan.topology, plan.capacity, errors)
            if plan.environment is not None and plan.topology is not None:
                approved_delta = plan.environment.target_nodes - plan.environment.current_nodes
                if approved_delta != plan.topology.nodes:
                    errors.append(
                        "Expansion topology delta does not match the approved node transition."
                    )

        if plan.action == "delete_environment":
            if plan.environment is None:
                errors.append("Deletion plan is missing an exact environment state snapshot.")
            elif plan.environment.target_nodes != 0:
                errors.append("Deletion plan must target zero nodes.")

        if plan.action == "scale_environment":
            target_nodes = self._as_int(plan.parameters.get("target_nodes"), default=0)
            if target_nodes < 3:
                errors.append("target_nodes must be at least 3.")
            if plan.environment is not None and target_nodes != plan.environment.target_nodes:
                errors.append("Scale parameter does not match the approved target node count.")

        if plan.action in {
            "delete_environment",
            "scale_environment",
            "add_cluster_node",
            "verify_cluster_health",
        }:
            workspace = str(plan.parameters.get("workspace", "")).strip()
            if not workspace:
                errors.append("A workspace name is required.")

        return PlanValidation(
            valid=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _validate_capacity(
        topology: TopologySpec,
        capacity: CapacitySnapshot,
        errors: list[str],
    ) -> None:
        if topology.total_cpu > capacity.cpu_available:
            errors.append(
                "Insufficient CPU: "
                f"plan requires {topology.total_cpu} vCPU, "
                f"but {capacity.cpu_available} vCPU is safely available."
            )
        if topology.total_ram_mb > capacity.ram_available_mb:
            errors.append(
                "Insufficient RAM: "
                f"plan requires {topology.total_ram_mb // 1024} GiB, "
                f"but {capacity.ram_available_mb // 1024} GiB is safely available."
            )
        if topology.total_storage_gib > capacity.storage_available_gib:
            errors.append(
                "Insufficient storage: "
                f"plan requires {topology.total_storage_gib} GiB, "
                f"but {capacity.storage_available_gib} GiB is safely available."
            )

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


class ApprovalManager:
    """Hold one pending plan and consume approval only for the exact same digest."""

    def __init__(self) -> None:
        self._pending: ExecutionPlan | None = None
        self._pending_digest: str | None = None

    @property
    def pending(self) -> ExecutionPlan | None:
        return self._pending

    def request(self, plan: ExecutionPlan) -> None:
        if not plan.requires_confirmation:
            raise ValueError(f"Action '{plan.action}' does not require confirmation")
        self._pending = plan
        self._pending_digest = plan.digest

    def cancel(self) -> ExecutionPlan | None:
        pending = self._pending
        self._pending = None
        self._pending_digest = None
        return pending

    def consume(self, expected_digest: str | None = None) -> ExecutionPlan:
        if self._pending is None:
            raise PermissionError("No infrastructure plan is awaiting approval")
        current_digest = self._pending.digest
        if self._pending_digest != current_digest:
            raise PermissionError("The infrastructure plan was mutated after it was displayed")
        if expected_digest is not None and self._pending_digest != expected_digest:
            raise PermissionError("The infrastructure plan changed after it was displayed")
        approved = self._pending
        self._pending = None
        self._pending_digest = None
        return approved


def classify_confirmation(message: str) -> Literal["approve", "reject", "other"]:
    """Classify a standalone reply while an exact plan is awaiting approval."""
    normalized = " ".join((message or "").strip().lower().split())
    if normalized in AFFIRMATIVE_RESPONSES:
        return "approve"
    if normalized in NEGATIVE_RESPONSES:
        return "reject"
    return "other"
