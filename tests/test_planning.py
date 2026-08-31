import pytest

from lab_ai_assistant.planning import (
    ApprovalManager,
    CapacitySnapshot,
    EnvironmentSnapshot,
    ExecutionPlan,
    PlanValidator,
    TopologySpec,
    classify_confirmation,
)


def deployment_plan(cpu_available: int = 16) -> ExecutionPlan:
    return ExecutionPlan(
        action="deploy_microcloud",
        parameters={"nodes": 3},
        summary="Create a storage demo",
        topology=TopologySpec(
            nodes=3,
            node_cpu=4,
            node_memory_mb=8192,
            root_disk_gib=40,
            ceph_disk_gib=50,
            ceph_disks_per_node=2,
            local_disk_gib=0,
        ),
        capacity=CapacitySnapshot(
            cpu_available=cpu_available,
            ram_available_mb=32 * 1024,
            storage_available_gib=600,
        ),
    )


def test_plan_validation_accounts_for_complete_capacity() -> None:
    validator = PlanValidator()

    assert validator.validate(deployment_plan()).valid
    rejected = validator.validate(deployment_plan(cpu_available=8))

    assert not rejected.valid
    assert any("CPU" in error for error in rejected.errors)


def test_approval_is_exact_and_one_shot() -> None:
    manager = ApprovalManager()
    plan = deployment_plan()
    manager.request(plan)

    with pytest.raises(PermissionError):
        manager.consume("different-digest")

    assert manager.consume(plan.digest) == plan
    with pytest.raises(PermissionError):
        manager.consume()


def test_approval_rejects_mutated_parameter_dictionary() -> None:
    manager = ApprovalManager()
    plan = deployment_plan()
    manager.request(plan)

    plan.parameters["nodes"] = 5

    with pytest.raises(PermissionError, match="mutated"):
        manager.consume()


def test_delete_plan_requires_and_hashes_exact_state_identity() -> None:
    validator = PlanValidator()
    unbound = ExecutionPlan(
        action="delete_environment",
        parameters={"workspace": "lab_microcloud"},
        summary="Delete lab",
    )
    environment = EnvironmentSnapshot(
        workspace="lab_microcloud",
        state_lineage="lineage-1",
        state_serial=7,
        current_nodes=3,
        target_nodes=0,
        storage_pool="default",
        lxd_project_name="cala-lab-microcloud-12345678",
    )
    bound = unbound.model_copy(update={"environment": environment})
    changed = bound.model_copy(
        update={"environment": environment.model_copy(update={"state_serial": 8})}
    )

    assert not validator.validate(unbound).valid
    assert validator.validate(bound).valid
    assert bound.digest != changed.digest


@pytest.mark.parametrize("message", ["yes", "evet", "confirm"])
def test_confirmation_classification(message: str) -> None:
    assert classify_confirmation(message) == "approve"
    assert classify_confirmation("no") == "reject"
    assert classify_confirmation("change the RAM") == "other"


def test_fully_segregated_topology_requires_two_distinct_ipv4_planes() -> None:
    topology = TopologySpec(
        nodes=3,
        node_cpu=2,
        node_memory_mb=4096,
        root_disk_gib=30,
        ceph_disk_gib=20,
        ceph_disks_per_node=1,
        network_mode="fully-segregated-4nic",
        ovn_underlay_cidr="172.28.42.0/24",
        ceph_network_cidr="172.29.42.0/24",
    )

    assert topology.network_mode == "fully-segregated-4nic"

    with pytest.raises(ValueError, match="requires ovn_underlay_cidr"):
        TopologySpec(
            nodes=3,
            node_cpu=2,
            node_memory_mb=4096,
            root_disk_gib=30,
            ceph_disk_gib=20,
            ceph_disks_per_node=1,
            network_mode="fully-segregated-4nic",
        )

    with pytest.raises(ValueError, match="must not overlap"):
        TopologySpec(
            **{
                **topology.model_dump(),
                "ceph_network_cidr": "172.28.42.128/25",
            }
        )


def test_segregated_plane_cidrs_must_fit_every_node() -> None:
    with pytest.raises(ValueError, match="usable addresses"):
        TopologySpec(
            nodes=8,
            node_cpu=2,
            node_memory_mb=4096,
            root_disk_gib=30,
            ceph_disk_gib=20,
            ceph_disks_per_node=1,
            network_mode="fully-segregated-4nic",
            ovn_underlay_cidr="172.28.42.0/28",
            ceph_network_cidr="172.29.42.0/28",
        )


def test_standard_topology_rejects_unused_plane_cidrs() -> None:
    with pytest.raises(ValueError, match="require fully-segregated-4nic"):
        TopologySpec(
            nodes=3,
            node_cpu=2,
            node_memory_mb=4096,
            root_disk_gib=30,
            ceph_disk_gib=20,
            ceph_disks_per_node=1,
            ovn_underlay_cidr="172.28.42.0/24",
        )
