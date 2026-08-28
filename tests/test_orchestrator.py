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
