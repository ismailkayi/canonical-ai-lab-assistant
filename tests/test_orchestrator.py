import ipaddress
import os
import time

import pytest

from lab_ai_assistant.orchestrator import LabOrchestrator
from lab_ai_assistant.planning import (
    LAB_OVERCOMMIT_POLICY,
    CapacitySnapshot,
    EnvironmentSnapshot,
    ExecutionPlan,
    PlanValidation,
    TopologySpec,
)
from lab_ai_assistant.tools import get_tool_definitions
from lab_ai_assistant.verification import ServiceCheck, VerificationReport


def host_state():
    return {
        "cpu_cores": 32,
        "ram_total_mb": 64 * 1024,
        "ram_available_mb": 60 * 1024,
        "storage_available_gib": 1000,
        "consumed_cpu": 0,
        "consumed_ram_gb": 0,
        "environments": [],
    }


def healthy_report() -> VerificationReport:
    return VerificationReport(
        workspace="lab_microcloud",
        status="healthy",
        expected_nodes=3,
        expected_osds=3,
        checks=(
            ServiceCheck(
                name="ceph-health",
                status="healthy",
                command_ok=True,
                detail="health=HEALTH_OK",
            ),
        ),
    )


def test_mutation_requires_exact_confirmation(config) -> None:
    orchestrator = LabOrchestrator(config)
    executed = []
    orchestrator._refresh_ai_environment_context = lambda: None
    orchestrator._collect_host_state = lambda force=False: host_state()
    orchestrator.cluster_verifier.workspace_exists = lambda _workspace: False
    orchestrator.cluster_verifier.verify = lambda _workspace, **_kwargs: healthy_report()
    orchestrator.ai_engine.chat = lambda _message: {
        "action": "deploy_microcloud",
        "parameters": {
            "nodes": 3,
            "node_cpu": 2,
            "node_memory_mb": 4096,
            "root_disk_gib": 30,
            "ceph_disk_gib": 20,
        },
        "message": "Create the lab",
    }
    orchestrator._execute_action = lambda action, params: (
        executed.append((action, params.copy())) or "success"
    )
    orchestrator._record_deployment = lambda *args: None
    orchestrator.ai_engine.feed_tool_result = lambda *_args: {
        "action": None,
        "message": "Deployment completed",
    }

    first = orchestrator._process_user_input("Create a three-node lab")

    assert first.startswith("__CONFIRM__:")
    assert not executed

    assert orchestrator._process_user_input("maybe").startswith("__CONFIRM__:")
    assert not executed

    result = orchestrator._process_user_input("yes")

    assert result == "Deployment completed"
    assert len(executed) == 1
    assert executed[0][1]["nodes"] == 3


def test_residual_capacity_subtracts_active_allocations(config) -> None:
    orchestrator = LabOrchestrator(config)
    capacity = orchestrator._capacity_snapshot(
        {
            "cpu_cores": 32,
            "ram_total_mb": 64 * 1024,
            "ram_available_mb": 4 * 1024,
            "storage_available_gib": 500,
            "consumed_cpu": 30,
            "consumed_ram_gb": 60,
        }
    )

    assert capacity.cpu_available == 0
    assert capacity.ram_available_mb == 0
    assert capacity.storage_available_gib == 480


def test_fresh_deploy_rejects_existing_workspace(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.workspace_exists = lambda workspace: workspace == "lab_microcloud"
    orchestrator.cluster_verifier.workspace_resource_count = lambda _workspace: 3

    with pytest.raises(ValueError, match="already contains 3 managed resource"):
        orchestrator._resolve_deployment_parameters(
            {"user_prefix": "lab", "nodes": 3},
            CapacitySnapshot(
                cpu_available=16,
                ram_available_mb=32 * 1024,
                storage_available_gib=500,
            ),
        )


def test_fresh_deploy_allows_empty_stale_workspace(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.workspace_exists = lambda workspace: workspace == "lab_microcloud"
    orchestrator.cluster_verifier.workspace_resource_count = lambda _workspace: 0

    resolved, topology = orchestrator._resolve_deployment_parameters(
        {"user_prefix": "lab", "nodes": 3},
        CapacitySnapshot(
            cpu_available=16,
            ram_available_mb=32 * 1024,
            storage_available_gib=500,
        ),
    )

    assert resolved["user_prefix"] == "lab"
    assert topology.nodes == 3


def test_confirmation_displays_environment_target_and_exact_parameters(config) -> None:
    orchestrator = LabOrchestrator(config)
    plan = ExecutionPlan(
        action="scale_environment",
        parameters={"workspace": "lab_microcloud", "target_nodes": 5},
        summary="Expand the lab",
        topology=TopologySpec(
            nodes=2,
            node_cpu=2,
            node_memory_mb=4096,
            root_disk_gib=30,
            ceph_disk_gib=20,
            ceph_disks_per_node=1,
        ),
        capacity=CapacitySnapshot(
            cpu_available=8,
            ram_available_mb=16 * 1024,
            storage_available_gib=200,
        ),
    )

    rendered = orchestrator._format_plan_for_confirmation(plan)

    assert "Environment: lab_microcloud" in rendered
    assert "New nodes (resource delta): 2" in rendered
    assert "target_nodes=5" in rendered
    assert "workspace=lab_microcloud" in rendered


def test_expansion_rejects_total_above_fifty_nodes(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.deployment_spec = lambda _workspace: {
        "node_count": 40,
        "node_cpu": 2,
        "node_memory_mb": 4096,
        "root_disk_gib": 30,
        "ceph_disk_gib": 20,
        "ceph_disks_per_node": 1,
        "local_disk_gib": 0,
        "lxd_storage_pool": "default",
        "resource_namespace": "1234abcd",
    }
    orchestrator.cluster_verifier.workspace_state = lambda _workspace: {
        "state_lineage": "lineage-1",
        "state_serial": 7,
        "current_nodes": 40,
        "storage_pool": "default",
    }

    with pytest.raises(ValueError, match="maximum is 50"):
        orchestrator._resolve_expansion_topology(
            "add_cluster_node",
            {"workspace": "lab_microcloud", "add_nodes": 11},
        )


def test_script_failure_closes_native_tool_call(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.ai_engine._pending_tool_call_id = "call-failed"
    orchestrator._execute_action = lambda *_args: "Script failed (exit 1): boom"
    orchestrator._record_deployment = lambda *args: None
    # Keep the diagnosis step offline; we assert on wiring, not model wording.
    orchestrator.ai_engine._call_inference = lambda *_args, **_kwargs: {
        "content": "Root cause: the workspace lock was still held."
    }
    plan = ExecutionPlan(
        action="delete_environment",
        parameters={"workspace": "lab_microcloud"},
        summary="Delete lab",
    )

    result = orchestrator._execute_approved_plan(plan)

    assert "failed" in result.lower()
    assert orchestrator.ai_engine._pending_tool_call_id is None

    tool_messages = [
        message
        for message in orchestrator.ai_engine.conversation_history
        if message.get("role") == "tool"
    ]
    assert tool_messages, "the native tool call must still be closed on failure"
    assert tool_messages[-1]["tool_call_id"] == "call-failed"
    assert "Script failed" in tool_messages[-1]["content"]

    # The deterministic summary stays authoritative, with AI analysis appended.
    assert "No background work is running" in result
    assert "Root cause: the workspace lock was still held." in result


def test_expansion_revalidation_rejects_changed_geometry(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator._collect_host_state = lambda force=False: host_state()
    original = TopologySpec(
        nodes=1,
        node_cpu=2,
        node_memory_mb=4096,
        root_disk_gib=30,
        ceph_disk_gib=20,
        ceph_disks_per_node=1,
    )
    changed = original.model_copy(update={"node_cpu": 4})
    environment = EnvironmentSnapshot(
        workspace="lab_microcloud",
        state_lineage="lineage-1",
        state_serial=7,
        current_nodes=3,
        target_nodes=4,
        storage_pool="default",
    )
    changed_environment = environment.model_copy(update={"state_serial": 8})
    orchestrator._workspace_snapshot = lambda *_args, **_kwargs: environment
    orchestrator._resolve_expansion_plan = lambda *_args: (changed, changed_environment)
    plan = ExecutionPlan(
        action="add_cluster_node",
        parameters={"workspace": "lab_microcloud", "add_nodes": 1},
        summary="Add node",
        topology=original,
        capacity=CapacitySnapshot(
            cpu_available=8,
            ram_available_mb=16 * 1024,
            storage_available_gib=200,
        ),
        environment=environment,
    )

    validation = orchestrator._revalidate_approved_plan(plan)

    assert not validation.valid
    assert "geometry changed" in validation.errors[0]


def test_operation_timeout_kills_child_process_group(config, tmp_path) -> None:
    script = tmp_path / "long-operation.sh"
    child_pid_file = tmp_path / "child.pid"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "(trap '' TERM; sleep 60) &\n"
        f"echo $! > {child_pid_file}\n"
        "printf 'partial-output-without-newline'\n"
        "wait\n"
    )
    script.chmod(0o755)
    config.prep_host_script = script
    config.operation_timeout = 1
    orchestrator = LabOrchestrator(config)

    result = orchestrator._execute_action("prep_host", {})

    assert result.startswith("Error: Operation timed out")
    child_pid = int(child_pid_file.read_text())
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"child process {child_pid} survived operation timeout")


def test_unknown_native_tool_call_is_closed(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.ai_engine._pending_tool_call_id = "call-unknown"

    result = orchestrator._run_agent_loop(
        "do something",
        {"action": "mystery_action", "parameters": {}, "message": "Unknown tool"},
    )

    assert result == "Unknown tool"
    assert orchestrator.ai_engine._pending_tool_call_id is None
    assert orchestrator.ai_engine.conversation_history[-1]["role"] == "tool"
    assert "Unknown or unsupported" in orchestrator.ai_engine.conversation_history[-1]["content"]


def test_max_round_native_tool_call_is_closed(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.ai_engine._pending_tool_call_id = "call-0"
    orchestrator._handle_local_tool = lambda *_args: "ok"
    counter = 0

    def another_tool(action, result):
        nonlocal counter
        orchestrator.ai_engine.record_tool_observation(action, result)
        counter += 1
        orchestrator.ai_engine._pending_tool_call_id = f"call-{counter}"
        return {
            "action": "inspect_host_environment",
            "parameters": {},
            "message": "Inspect again",
        }

    orchestrator.ai_engine.feed_tool_result = another_tool

    orchestrator._run_agent_loop(
        "inspect repeatedly",
        {
            "action": "inspect_host_environment",
            "parameters": {},
            "message": "Inspect",
        },
    )

    assert counter == 5
    assert orchestrator.ai_engine._pending_tool_call_id is None
    assert (
        "Maximum tool reasoning rounds"
        in orchestrator.ai_engine.conversation_history[-1]["content"]
    )


def test_host_state_prefers_exact_default_storage_pool(config) -> None:
    orchestrator = LabOrchestrator(config)

    def fake_host_command(command, timeout=10):
        if "nproc" in command:
            return "8"
        if "MemTotal" in command:
            return "16384"
        if "MemAvailable" in command:
            return "12288"
        if "storage list" in command:
            return "aaa,bbb,default"
        if "network list" in command:
            return "lxdbr0,other"
        return ""

    selected_pools = []
    orchestrator._run_host_cmd = fake_host_command
    orchestrator._get_pool_available_gib = lambda pool: selected_pools.append(pool) or 500
    orchestrator._collect_environment_usage = lambda: ([], 0, 0, True)

    state = orchestrator._collect_host_state(force=True)

    assert state["primary_pool"] == "default"
    assert selected_pools == ["default"]


def test_approval_revalidation_and_execution_share_infrastructure_lock(
    config,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    orchestrator = LabOrchestrator(config)
    plan = ExecutionPlan(
        action="delete_environment",
        parameters={"workspace": "lab_microcloud"},
        summary="Delete lab",
        environment=EnvironmentSnapshot(
            workspace="lab_microcloud",
            state_lineage="lineage-1",
            state_serial=7,
            current_nodes=3,
            target_nodes=0,
            storage_pool="default",
        ),
    )
    orchestrator.approval_manager.request(plan)
    observed_fds = []

    def revalidate(_plan):
        observed_fds.append(orchestrator._infrastructure_lock_fd)
        return PlanValidation(valid=True)

    def execute(_plan):
        observed_fds.append(orchestrator._infrastructure_lock_fd)
        return "done"

    orchestrator._revalidate_approved_plan = revalidate
    orchestrator._execute_approved_plan = execute

    assert orchestrator._handle_pending_confirmation("yes") == "done"
    assert len(observed_fds) == 2
    assert observed_fds[0] is not None
    assert observed_fds[0] == observed_fds[1]
    assert orchestrator._infrastructure_lock_fd is None


def test_child_script_receives_inherited_infrastructure_lock(config, tmp_path) -> None:
    script = tmp_path / "inspect-lock.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "fd=${LAB_AI_TERRAFORM_LOCK_FD:-}\n"
        'if [[ -n "$fd" && -e "/proc/$$/fd/$fd" ]]; then echo inherited; else exit 1; fi\n'
    )
    script.chmod(0o755)
    config.prep_host_script = script
    orchestrator = LabOrchestrator(config)

    with orchestrator._infrastructure_lock():
        result = orchestrator._execute_action("prep_host", {})

    assert "inherited" in result


def test_empty_native_tool_call_is_closed(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.ai_engine._pending_tool_call_id = "call-empty"

    result = orchestrator._run_agent_loop("do something", {"action": None, "message": ""})

    assert "empty response" in result.lower()
    assert orchestrator.ai_engine._pending_tool_call_id is None
    assert "valid action name" in orchestrator.ai_engine.conversation_history[-1]["content"]


def test_large_failure_output_is_bounded_before_diagnosis(config) -> None:
    """A huge Ansible log must not flood the model context."""
    orchestrator = LabOrchestrator(config)
    huge = "filler line\n" * 5000 + "FATAL: no route to host 10.0.5.22:9443"

    evidence = orchestrator._bound_failure_evidence(huge)

    assert len(evidence) < len(huge)
    assert "truncated for context safety" in evidence
    # The decisive error sits at the end of a run, so the tail must survive.
    assert "FATAL: no route to host 10.0.5.22:9443" in evidence


def test_small_failure_output_is_passed_through_untouched(config) -> None:
    orchestrator = LabOrchestrator(config)

    assert orchestrator._bound_failure_evidence("Script failed (exit 1): boom") == (
        "Script failed (exit 1): boom"
    )


def test_bootstrap_passes_configured_engine_to_installer(config, monkeypatch) -> None:
    """Bootstrap must install the engine the runtime will actually talk to."""
    config.inference_engine = "qwen3"
    orchestrator = LabOrchestrator(config)
    commands: list[list[str]] = []

    class _Result:
        returncode = 0

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        return _Result()

    monkeypatch.setattr("lab_ai_assistant.orchestrator.subprocess.run", fake_run)
    orchestrator.bootstrap_host()

    installer = [cmd for cmd in commands if "install_inference_snap.sh" in cmd[1]]
    assert installer, "the inference installer must run during bootstrap"
    assert installer[0][2:] == ["--engine", "qwen3"]

    prep = [cmd for cmd in commands if "prep_host.sh" in cmd[1]]
    assert prep and prep[0][2:] == [], "prep_host takes no arguments"


def test_explicit_tier_is_not_overridden_by_workload_text(config) -> None:
    """The model's explicit sizing decision must survive unrelated prose."""
    orchestrator = LabOrchestrator(config)

    # "dev" in the workload used to silently downgrade an explicit "large".
    assert orchestrator._tier_to_profile("large", "dev sandbox") == "performance"
    assert orchestrator._tier_to_profile("medium", "poc") == "balanced"
    assert orchestrator._tier_to_profile("minimal", "production HA") == "conservative"


def test_every_advertised_tier_maps_to_a_profile(config) -> None:
    """Each tier the tool schema offers must resolve to a real sizing profile."""
    orchestrator = LabOrchestrator(config)
    schema = get_tool_definitions()
    deploy = next(t for t in schema["tools"] if t["name"] == "deploy_microcloud")
    tiers = deploy["parameters"]["properties"]["sizing_tier"]["enum"]

    for tier in tiers:
        assert orchestrator._tier_to_profile(tier) in {
            "conservative",
            "balanced",
            "performance",
        }


def test_tier_is_inferred_only_when_absent(config) -> None:
    orchestrator = LabOrchestrator(config)

    assert orchestrator._tier_to_profile(None, "a small poc lab") == "conservative"
    assert orchestrator._tier_to_profile("", "production cluster") == "performance"
    assert orchestrator._tier_to_profile(None, "") == "balanced"


def test_model_prose_cannot_trigger_a_destructive_action(config) -> None:
    """A stray word in the model's own explanation must not select a delete."""
    orchestrator = LabOrchestrator(config)

    resolved = orchestrator._resolve_tool_action(
        "tool_call",
        message="I will remove the guesswork and list what you have.",
        reasoning="Nothing here should destroy anything.",
        parameters={"workspace": "lab_microcloud"},
        user_input="show my labs",
    )

    assert resolved == "list_environments"


def test_unknown_action_with_workspace_is_not_assumed_to_be_delete(config) -> None:
    orchestrator = LabOrchestrator(config)

    resolved = orchestrator._resolve_tool_action(
        "tool_call",
        message="",
        reasoning="",
        parameters={"workspace": "lab_microcloud"},
        user_input="tell me about microcloud storage",
    )

    assert resolved is None


def test_user_intent_still_routes_known_lifecycle_requests(config) -> None:
    orchestrator = LabOrchestrator(config)
    resolve = orchestrator._resolve_tool_action

    assert resolve("tool_call", "", "", {}, "delete lab_microcloud") == "delete_environment"
    assert resolve("tool_call", "", "", {}, "add nodes to my cluster") == "add_cluster_node"
    assert resolve("tool_call", "", "", {}, "scale it to 5 nodes") == "scale_environment"
    assert resolve("tool_call", "", "", {}, "list my labs") == "list_environments"
    assert resolve("tool_call", "", "", {}, "check cluster health") == "verify_cluster_health"


def test_add_nodes_is_not_swallowed_by_the_scale_rule(config) -> None:
    """Ordering matters: 'add nodes' is more specific than 'more nodes'."""
    orchestrator = LabOrchestrator(config)

    resolved = orchestrator._resolve_tool_action(
        "tool_call", "", "", {}, "add nodes so I have more nodes"
    )

    assert resolved == "add_cluster_node"


def test_standard_network_mode_remains_the_default(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.workspace_exists = lambda _workspace: False
    capacity = CapacitySnapshot(
        cpu_available=32,
        ram_available_mb=64 * 1024,
        storage_available_gib=1000,
    )

    resolved, topology = orchestrator._resolve_deployment_parameters(
        {"nodes": 3, "user_prefix": "default-network"},
        capacity,
    )

    assert resolved["network_mode"] == "standard-2nic"
    assert "ovn_underlay_cidr" not in resolved
    assert topology.network_mode == "standard-2nic"


def test_segregated_network_mode_resolves_exact_non_overlapping_cidrs(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.workspace_exists = lambda _workspace: False
    occupied = [ipaddress.ip_network("172.28.10.0/24")]
    orchestrator._occupied_ipv4_networks = lambda: occupied
    capacity = CapacitySnapshot(
        cpu_available=32,
        ram_available_mb=64 * 1024,
        storage_available_gib=1000,
    )

    parameters = {
        "nodes": 3,
        "user_prefix": "network-training",
        "network_mode": "fully-segregated-4nic",
    }
    first, topology = orchestrator._resolve_deployment_parameters(parameters, capacity)
    second, _ = orchestrator._resolve_deployment_parameters(parameters, capacity)

    ovn = ipaddress.ip_network(first["ovn_underlay_cidr"])
    ceph = ipaddress.ip_network(first["ceph_network_cidr"])
    assert first["ovn_underlay_cidr"] == second["ovn_underlay_cidr"]
    assert first["ceph_network_cidr"] == second["ceph_network_cidr"]
    assert not ovn.overlaps(ceph)
    assert not ovn.overlaps(occupied[0])
    assert topology.network_mode == "fully-segregated-4nic"


def test_explicit_segregated_cidr_cannot_overlap_host_network(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.workspace_exists = lambda _workspace: False
    orchestrator._occupied_ipv4_networks = lambda: [ipaddress.ip_network("172.28.42.0/24")]
    capacity = CapacitySnapshot(
        cpu_available=32,
        ram_available_mb=64 * 1024,
        storage_available_gib=1000,
    )

    with pytest.raises(ValueError, match="overlaps"):
        orchestrator._resolve_deployment_parameters(
            {
                "nodes": 3,
                "network_mode": "fully-segregated-4nic",
                "ovn_underlay_cidr": "172.28.42.0/24",
                "ceph_network_cidr": "172.29.42.0/24",
            },
            capacity,
        )


def test_confirmation_displays_segregated_network_geometry(config) -> None:
    orchestrator = LabOrchestrator(config)
    plan = ExecutionPlan(
        action="deploy_microcloud",
        parameters={
            "nodes": 3,
            "network_mode": "fully-segregated-4nic",
            "ovn_underlay_cidr": "172.28.42.0/24",
            "ceph_network_cidr": "172.29.42.0/24",
        },
        summary="Create a network training lab",
        topology=TopologySpec(
            nodes=3,
            node_cpu=2,
            node_memory_mb=4096,
            root_disk_gib=30,
            ceph_disk_gib=20,
            ceph_disks_per_node=1,
            network_mode="fully-segregated-4nic",
            ovn_underlay_cidr="172.28.42.0/24",
            ceph_network_cidr="172.29.42.0/24",
        ),
    )

    rendered = orchestrator._format_plan_for_confirmation(plan)

    assert "fully-segregated-4nic" in rendered
    assert "OVN underlay 172.28.42.0/24" in rendered
    assert "Ceph 172.29.42.0/24" in rendered


def test_expansion_inherits_persisted_segregated_network_geometry(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.deployment_spec = lambda _workspace: {
        "node_count": 3,
        "node_cpu": 2,
        "node_memory_mb": 4096,
        "root_disk_gib": 30,
        "ceph_disk_gib": 20,
        "ceph_disks_per_node": 1,
        "local_disk_gib": 0,
        "lxd_storage_pool": "default",
        "resource_namespace": "1234abcd",
        "network_mode": "fully-segregated-4nic",
        "ovn_underlay_cidr": "172.28.42.0/24",
        "ceph_network_cidr": "172.29.42.0/24",
    }
    orchestrator.cluster_verifier.workspace_state = lambda _workspace: {
        "state_lineage": "lineage-1",
        "state_serial": 7,
        "current_nodes": 3,
        "storage_pool": "default",
    }

    topology = orchestrator._resolve_expansion_topology(
        "add_cluster_node",
        {"workspace": "lab_microcloud", "add_nodes": 2},
    )

    assert topology.nodes == 2
    assert topology.network_mode == "fully-segregated-4nic"
    assert topology.ovn_underlay_cidr == "172.28.42.0/24"
    assert topology.ceph_network_cidr == "172.29.42.0/24"


def test_expansion_rejects_segregated_cidr_too_small_for_target(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.deployment_spec = lambda _workspace: {
        "node_count": 3,
        "node_cpu": 2,
        "node_memory_mb": 4096,
        "root_disk_gib": 30,
        "ceph_disk_gib": 20,
        "ceph_disks_per_node": 1,
        "local_disk_gib": 0,
        "lxd_storage_pool": "default",
        "resource_namespace": "1234abcd",
        "network_mode": "fully-segregated-4nic",
        "ovn_underlay_cidr": "172.28.42.0/28",
        "ceph_network_cidr": "172.29.42.0/28",
    }
    orchestrator.cluster_verifier.workspace_state = lambda _workspace: {
        "state_lineage": "lineage-1",
        "state_serial": 7,
        "current_nodes": 3,
        "storage_pool": "default",
    }

    with pytest.raises(ValueError, match="no usable address"):
        orchestrator._resolve_expansion_topology(
            "add_cluster_node",
            {"workspace": "lab_microcloud", "add_nodes": 3},
        )


def test_version_two_environment_is_destroy_only(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.deployment_spec = lambda _workspace: {
        "node_count": 3,
        "node_cpu": 2,
        "node_memory_mb": 4096,
        "root_disk_gib": 30,
        "ceph_disk_gib": 20,
        "ceph_disks_per_node": 1,
        "local_disk_gib": 0,
        "lxd_storage_pool": "default",
        "network_mode": "standard-2nic",
    }

    with pytest.raises(ValueError, match="deleted safely.*add/scale"):
        orchestrator._resolve_expansion_topology(
            "add_cluster_node",
            {"workspace": "lab_microcloud", "add_nodes": 1},
        )


def test_revalidation_blocks_a_new_network_overlap_after_approval(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.workspace_exists = lambda _workspace: False
    orchestrator._occupied_ipv4_networks = lambda: [ipaddress.ip_network("172.28.42.0/24")]
    plan = ExecutionPlan(
        action="deploy_microcloud",
        parameters={
            "nodes": 3,
            "network_mode": "fully-segregated-4nic",
            "ovn_underlay_cidr": "172.28.42.0/24",
            "ceph_network_cidr": "172.29.42.0/24",
        },
        summary="Create a network lab",
        topology=TopologySpec(
            nodes=3,
            node_cpu=2,
            node_memory_mb=4096,
            root_disk_gib=30,
            ceph_disk_gib=20,
            ceph_disks_per_node=1,
            network_mode="fully-segregated-4nic",
            ovn_underlay_cidr="172.28.42.0/24",
            ceph_network_cidr="172.29.42.0/24",
        ),
        capacity=CapacitySnapshot(
            cpu_available=8,
            ram_available_mb=16 * 1024,
            storage_available_gib=200,
        ),
    )

    validation = orchestrator._revalidate_approved_plan(plan)

    assert not validation.valid
    assert "availability changed after approval" in validation.errors[0]


def test_custom_topology_proposal_keeps_the_network_choice(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator._collect_host_state = lambda force=False: host_state()

    result = orchestrator._handle_local_tool(
        "propose_custom_topology",
        {
            "node_count": 3,
            "node_cpu": 2,
            "node_ram_gb": 4,
            "root_disk_gb": 30,
            "ceph_disk_gb": 20,
            "network_mode": "fully-segregated-4nic",
            "reasoning": "Teach isolated traffic planes.",
        },
    )

    assert "Network layout   : fully-segregated-4nic" in result
    assert "OVN underlay / Ceph public+internal" in result
    assert "collision-checked in the exact deploy plan" in result


def test_resource_namespace_is_deterministic_and_workspace_specific(config) -> None:
    orchestrator = LabOrchestrator(config)

    first = orchestrator._resource_namespace("training_a_microcloud")
    repeated = orchestrator._resource_namespace("training_a_microcloud")
    similar = orchestrator._resource_namespace("training_b_microcloud")

    assert first == repeated
    assert first != similar
    assert len(first) == 8
    assert all(character in "0123456789abcdef" for character in first)


def test_lxd_manifest_contains_every_planned_name(config) -> None:
    orchestrator = LabOrchestrator(config)
    manifest = orchestrator._build_lxd_resource_manifest(
        workspace="training_microcloud",
        storage_pool="default",
        nodes=2,
        ceph_disks_per_node=2,
        local_disk_enabled=True,
        network_mode="fully-segregated-4nic",
        resource_namespace="1234abcd",
    )

    assert manifest.profiles == ("training-microcloud-iac-base",)
    assert manifest.networks == (
        "ca-1234abcd-up",
        "ca-1234abcd-ov",
        "ca-1234abcd-ce",
    )
    assert manifest.instances == (
        "training-microcloud-node-1",
        "training-microcloud-node-2",
    )
    assert manifest.volumes == (
        "training-microcloud-ceph-1-1",
        "training-microcloud-ceph-1-2",
        "training-microcloud-ceph-2-1",
        "training-microcloud-ceph-2-2",
        "training-microcloud-local-1",
        "training-microcloud-local-2",
    )


def test_deployment_rejects_exact_unmanaged_lxd_collision(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.workspace_exists = lambda _workspace: False
    orchestrator.cluster_verifier.lxd_name_conflicts = lambda _manifest: (
        "instance:lab-microcloud-node-2",
        "volume:lab-microcloud-ceph-1-1",
    )
    capacity = CapacitySnapshot(
        cpu_available=32,
        ram_available_mb=64 * 1024,
        storage_available_gib=1000,
    )

    with pytest.raises(ValueError, match="instance:lab-microcloud-node-2"):
        orchestrator._resolve_deployment_parameters(
            {"nodes": 3, "user_prefix": "lab"},
            capacity,
        )


def test_deployment_plan_persists_resolved_resource_namespace(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator.cluster_verifier.workspace_exists = lambda _workspace: False
    capacity = CapacitySnapshot(
        cpu_available=32,
        ram_available_mb=64 * 1024,
        storage_available_gib=1000,
    )

    resolved, _ = orchestrator._resolve_deployment_parameters(
        {"nodes": 3, "user_prefix": "training"},
        capacity,
    )

    assert resolved["resource_namespace"] == orchestrator._resource_namespace("training_microcloud")


def lab_overcommit_plan(
    *,
    storage_available_gib: int = 500,
    runtime_ram_available_mb: int = 32 * 1024,
    allocations_complete: bool = True,
) -> ExecutionPlan:
    return ExecutionPlan(
        action="deploy_microcloud",
        parameters={
            "nodes": 3,
            "user_prefix": "secondlab",
            "resource_namespace": "1234abcd",
        },
        summary="Create a second training lab",
        topology=TopologySpec(
            nodes=3,
            node_cpu=2,
            node_memory_mb=4096,
            root_disk_gib=30,
            ceph_disk_gib=20,
            ceph_disks_per_node=1,
        ),
        capacity=CapacitySnapshot(
            cpu_available=2,
            ram_available_mb=8 * 1024,
            storage_available_gib=storage_available_gib,
            cpu_total=32,
            cpu_allocated=24,
            ram_total_mb=58 * 1024,
            ram_allocated_mb=50 * 1024,
            runtime_ram_available_mb=runtime_ram_available_mb,
            allocations_complete=allocations_complete,
        ),
    )


def test_cpu_and_ram_shortage_can_build_bounded_lab_overcommit(config) -> None:
    orchestrator = LabOrchestrator(config)
    strict = lab_overcommit_plan()
    validation = orchestrator.plan_validator.validate(strict)

    candidate = orchestrator._build_lab_overcommit_plan(strict, validation.errors)

    assert candidate is not None
    assert candidate.capacity.policy == LAB_OVERCOMMIT_POLICY
    assert candidate.capacity.cpu_available == 24  # 32 * 1.5 - 24
    assert candidate.capacity.ram_available_mb == int(58 * 1024 * 1.25) - 50 * 1024
    assert candidate.topology == strict.topology
    assert candidate.parameters == strict.parameters


def test_storage_or_incomplete_allocations_never_use_overcommit(config) -> None:
    orchestrator = LabOrchestrator(config)
    storage_short = lab_overcommit_plan(storage_available_gib=100)
    storage_validation = orchestrator.plan_validator.validate(storage_short)
    assert any("Insufficient storage:" in error for error in storage_validation.errors)
    assert (
        orchestrator._build_lab_overcommit_plan(
            storage_short,
            storage_validation.errors,
        )
        is None
    )

    incomplete = lab_overcommit_plan(allocations_complete=False)
    incomplete_validation = orchestrator.plan_validator.validate(incomplete)
    assert (
        orchestrator._build_lab_overcommit_plan(
            incomplete,
            incomplete_validation.errors,
        )
        is None
    )


def test_ram_runtime_headroom_blocks_candidate(config) -> None:
    orchestrator = LabOrchestrator(config)
    plan = lab_overcommit_plan(runtime_ram_available_mb=4 * 1024)
    validation = orchestrator.plan_validator.validate(plan)

    assert orchestrator._build_lab_overcommit_plan(plan, validation.errors) is None


def test_ai_recommendation_creates_explicit_overcommit_confirmation(config) -> None:
    orchestrator = LabOrchestrator(config)
    strict = lab_overcommit_plan()
    orchestrator._build_execution_plan = lambda *_args, **_kwargs: strict
    orchestrator.ai_engine.assess_lab_overcommit = lambda *_args: {
        "recommend": True,
        "rationale": "Short training with no simultaneous peak load.",
    }

    result = orchestrator._run_agent_loop(
        "create a short training lab",
        {
            "action": "deploy_microcloud",
            "parameters": {"nodes": 3},
            "message": "Create the lab.",
        },
    )

    assert result.startswith("__CONFIRM__:")
    assert "OVERCOMMIT WARNING" in result
    assert "Approve this exact overcommit risk-bound plan?" in result
    assert "approve overcommit" not in result.lower()
    assert orchestrator.approval_manager.pending.capacity.policy == LAB_OVERCOMMIT_POLICY


def test_ai_decline_returns_safer_recommendation_without_pending_plan(config) -> None:
    orchestrator = LabOrchestrator(config)
    strict = lab_overcommit_plan()
    orchestrator._build_execution_plan = lambda *_args, **_kwargs: strict
    orchestrator.ai_engine.assess_lab_overcommit = lambda *_args: {
        "recommend": False,
        "rationale": "Stop another lab before this benchmark.",
    }

    result = orchestrator._run_agent_loop(
        "create a benchmark lab",
        {
            "action": "deploy_microcloud",
            "parameters": {"nodes": 3},
            "message": "Create it.",
        },
    )

    assert "Stop another lab" in result
    assert orchestrator.approval_manager.pending is None


def test_normal_yes_approves_overcommit_after_warning(config) -> None:
    orchestrator = LabOrchestrator(config)
    strict = lab_overcommit_plan()
    validation = orchestrator.plan_validator.validate(strict)
    plan = orchestrator._build_lab_overcommit_plan(strict, validation.errors)
    assert plan is not None
    orchestrator.approval_manager.request(plan)
    orchestrator._revalidate_approved_plan = lambda _plan: PlanValidation(valid=True)
    orchestrator._execute_approved_plan = lambda _plan: "executed"

    assert orchestrator._process_user_input("yes") == "executed"


def test_overcommit_revalidation_rejects_changed_allocation(config) -> None:
    orchestrator = LabOrchestrator(config)
    strict = lab_overcommit_plan()
    validation = orchestrator.plan_validator.validate(strict)
    plan = orchestrator._build_lab_overcommit_plan(strict, validation.errors)
    assert plan is not None
    orchestrator.cluster_verifier.workspace_exists = lambda _workspace: False
    state = host_state()
    state.update(
        {
            "cpu_cores": 32,
            "ram_total_mb": 58 * 1024,
            "ram_available_mb": 32 * 1024,
            "consumed_cpu": 25,
            "consumed_ram_mb": 50 * 1024,
            "allocations_complete": True,
        }
    )
    orchestrator._collect_host_state = lambda force=False: state

    result = orchestrator._revalidate_approved_plan(plan)

    assert not result.valid
    assert "changed after" in result.errors[0]


def test_all_project_allocations_are_exact_and_fail_closed(config) -> None:
    orchestrator = LabOrchestrator(config)
    orchestrator._run_host_cmd_checked = lambda *_args, **_kwargs: (
        "default,lab-node-1,RUNNING,2,2047MiB\n" 'other,"unrelated,instance",RUNNING,"0-3,6",1.5GiB'
    )

    environments, cpu, ram_mb, complete = orchestrator._collect_environment_usage()

    assert complete
    assert cpu == 7
    assert ram_mb == 2047 + 1536
    assert environments[0]["name"] == "lab"
    assert orchestrator._parse_memory_mb("50%") is None

    orchestrator._run_host_cmd_checked = lambda *_args, **_kwargs: ("default,unbounded,RUNNING,,")
    _, _, _, complete = orchestrator._collect_environment_usage()
    assert not complete
