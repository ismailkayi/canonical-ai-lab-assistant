import os
import time

import pytest

from lab_ai_assistant.orchestrator import LabOrchestrator
from lab_ai_assistant.planning import (
    CapacitySnapshot,
    EnvironmentSnapshot,
    ExecutionPlan,
    PlanValidation,
    TopologySpec,
)
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
    plan = ExecutionPlan(
        action="delete_environment",
        parameters={"workspace": "lab_microcloud"},
        summary="Delete lab",
    )

    result = orchestrator._execute_approved_plan(plan)

    assert "failed" in result.lower()
    assert orchestrator.ai_engine._pending_tool_call_id is None
    assert orchestrator.ai_engine.conversation_history[-1]["role"] == "tool"
    assert "Script failed" in orchestrator.ai_engine.conversation_history[-1]["content"]


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
    orchestrator._collect_environment_usage = lambda: []

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
