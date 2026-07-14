"""Lab orchestration and execution layer for custom-topology MicroCloud workflows."""

import fcntl
import json
import logging
import os
import re
import select
import signal
import subprocess
import textwrap
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from lab_ai_assistant.ai_engine import AIEngine
from lab_ai_assistant.config import Config
from lab_ai_assistant.doc_fetcher import DocFetcher
from lab_ai_assistant.planning import (
    MUTATING_ACTIONS,
    ApprovalManager,
    CapacitySnapshot,
    EnvironmentSnapshot,
    ExecutionPlan,
    PlanValidation,
    PlanValidator,
    TopologySpec,
    classify_confirmation,
)
from lab_ai_assistant.sizing import SizingAdvisor, SizingTier
from lab_ai_assistant.tools import get_tool_definitions, validate_tool_parameters
from lab_ai_assistant.ui import ChatUI
from lab_ai_assistant.verification import ClusterVerifier

logger = logging.getLogger(__name__)

UI_WIDTH = 78


class LabOrchestrator:
    """Main orchestration layer for MicroCloud lab automation."""

    def __init__(self, config: Config):
        self.config = config
        self.ai_engine = AIEngine(config)
        self.sizing_advisor = SizingAdvisor()
        self.doc_fetcher = DocFetcher(cache_dir=config.state_dir)
        self.deployment_history: list[dict[str, Any]] = []
        self.ui = ChatUI()
        self.plan_validator = PlanValidator()
        self.approval_manager = ApprovalManager()
        self.cluster_verifier = ClusterVerifier(config.repo_root / "terraform")
        # Cache for host-state grounding so we don't re-shell out every turn.
        self._host_state_cache: dict[str, Any] | None = None
        self._host_state_cache_ts: float = 0.0
        self._host_state_ttl: float = 60.0
        self._infrastructure_lock_fd: int | None = None
        self._load_history()

    def bootstrap_host(self) -> str:
        """Prepare the host and install the inference snap.

        Streams output live so the user can see progress and respond to sudo prompts.
        """
        for script in [self.config.prep_host_script, self.config.install_inference_script]:
            if not script.exists():
                print(f"[warn] Script not found, skipping: {script}")
                continue
            print(f"\n--- {script.name} ---")
            result = subprocess.run(
                ["bash", str(script)],
                cwd=self.config.repo_root,
                # No capture_output — output goes directly to the terminal
            )
            if result.returncode != 0:
                raise RuntimeError(f"{script.name} failed (exit {result.returncode})")
        return "Bootstrap complete. Run 'lab-ai check' to verify the inference service."

    def start_chat(self):
        """Start interactive chat session."""
        if not self.ai_engine.is_available():
            raise RuntimeError(
                f"Inference engine not available at {self.config.inference_host}\n"
                "Please verify the snap service is running and INFERENCE_HOST points to the correct endpoint."
            )

        self.ui.print_welcome()

        while True:
            try:
                user_input = self.ui.get_user_input()
                if not user_input:
                    continue
                if user_input.lower() == "quit":
                    self.ui.print_ai_status("Session ended. Goodbye!")
                    break
                if user_input.lower() == "help":
                    self.ui.print_help()
                    continue
                if user_input.lower().startswith("sizing"):
                    self.ui.print_ai_response(self.sizing_advisor.describe_tiers())
                    continue

                with self.ui.thinking_indicator("AI is analyzing your request"):
                    response = self._process_user_input(user_input)

                self._print_assistant_response(response)

            except KeyboardInterrupt:
                self.ui.print_ai_status("Session ended. Goodbye!")
                break
            except Exception as exc:
                logger.error(f"Error in chat loop: {exc}")
                self.ui.print_error(str(exc))

    def _process_user_input(self, user_message: str) -> str:
        """Process user input through the agentic loop.

        The loop runs until the AI returns no tool call (final answer) or
        the maximum number of tool rounds is reached.
        """
        pending_response = self._handle_pending_confirmation(user_message)
        if pending_response is not None:
            return pending_response

        # Ground the model in real host capacity + active environments before it
        # reasons. Cached for a short TTL so this is cheap on every turn.
        self._refresh_ai_environment_context()

        ai_response = self.ai_engine.chat(user_message)
        return self._run_agent_loop(user_message, ai_response)

    def _run_agent_loop(self, user_message: str, ai_response: dict[str, Any]) -> str:
        """Run bounded AI/tool iterations, requiring a validated plan for mutations."""
        max_tool_rounds = 5

        for _round in range(max_tool_rounds):
            if ai_response.get("error"):
                return str(ai_response.get("content", "An error occurred"))

            action = ai_response.get("action")
            message = ai_response.get("message") or ai_response.get("content", "")
            reasoning = ai_response.get("reasoning", "")

            # No tool call → this is the final user-facing answer
            if not action:
                self.ai_engine.cancel_pending_tool_call(
                    "unknown",
                    "Native tool call did not include a valid action name",
                )
                return self._compose_user_facing_response(message, reasoning)

            # Show AI's intermediate plan/reasoning
            if message or reasoning:
                rendered_plan = self._compose_user_facing_response(message, reasoning)
                if rendered_plan:
                    self.ui.print_ai_plan(rendered_plan)

            parameters = ai_response.get("parameters", {})
            requested_action = str(action)
            action = self._resolve_tool_action(action, message, reasoning, parameters, user_message)

            if not action:
                self.ai_engine.cancel_pending_tool_call(
                    requested_action,
                    "Unknown or unsupported tool action",
                )
                return self._compose_user_facing_response(message, reasoning)

            is_valid, error_msg = validate_tool_parameters(action, parameters)
            if not is_valid:
                feedback = f"Error: tool request rejected by schema validation: {error_msg}"
                ai_response = self.ai_engine.feed_tool_result(action, feedback)
                continue

            if action in MUTATING_ACTIONS:
                try:
                    plan = self._build_execution_plan(action, parameters, message, reasoning)
                except (TypeError, ValueError) as exc:
                    feedback = f"Error: execution plan could not be built: {exc}"
                    ai_response = self.ai_engine.feed_tool_result(action, feedback)
                    continue

                validation = self.plan_validator.validate(plan)
                if not validation.valid:
                    feedback = self._format_plan_validation_failure(validation.errors)
                    ai_response = self.ai_engine.feed_tool_result(action, feedback)
                    continue

                self.approval_manager.request(plan)
                return f"__CONFIRM__:{self._format_plan_for_confirmation(plan)}"

            # Show tool execution in the UI
            self.ui.print_tool_call(action)

            # Execute the tool
            tool_result = self._handle_local_tool(action, parameters)
            if tool_result is None:
                logger.info(f"Executing action: {action} params={parameters}")
                tool_result = self._execute_action(action, parameters)
                self._record_deployment(action, parameters, tool_result)

            if self._is_failed_tool_result(tool_result):
                self.ai_engine.record_tool_observation(action, tool_result)
                return self._compose_failed_tool_response(action, tool_result)

            tool_result_for_ai = self._prepare_tool_result_for_ai(action, tool_result)

            # Feed result back to AI for synthesis (closes the loop)
            self.ui.print_phase("analyzing", "Processing tool results...")
            ai_response = self.ai_engine.feed_tool_result(action, tool_result_for_ai)

        # Close a final native tool call before returning at the bounded loop limit.
        final_action = str(ai_response.get("action") or "tool")
        self.ai_engine.cancel_pending_tool_call(
            final_action,
            "Maximum tool reasoning rounds reached before execution",
        )
        return self._compose_user_facing_response(
            ai_response.get("message")
            or ai_response.get("content", "Reached maximum reasoning rounds."),
            ai_response.get("reasoning", ""),
        )

    def _handle_pending_confirmation(self, user_message: str) -> str | None:
        """Handle approval replies before allowing the model to see the next turn."""
        pending = self.approval_manager.pending
        if pending is None:
            return None

        decision = classify_confirmation(user_message)
        if decision == "reject":
            cancelled = self.approval_manager.cancel()
            action = cancelled.action if cancelled else pending.action
            self.ai_engine.cancel_pending_tool_call(action, "User rejected the pending plan")
            return f"Cancelled the pending {action.replace('_', ' ')} plan. Nothing was changed."

        if decision != "approve":
            return (
                "__CONFIRM__:A plan is already awaiting approval.\n\n"
                f"{self._format_plan_for_confirmation(pending)}"
            )

        approved = self.approval_manager.consume(expected_digest=pending.digest)
        with self._infrastructure_lock():
            current_validation = self._revalidate_approved_plan(approved)
            if not current_validation.valid:
                reason = self._format_plan_validation_failure(current_validation.errors)
                self.ai_engine.cancel_pending_tool_call(approved.action, reason)
                return (
                    "The host changed after this plan was prepared, so execution was blocked.\n\n"
                    f"{reason}"
                )

            return self._execute_approved_plan(approved)

    @contextmanager
    def _infrastructure_lock(self) -> Iterator[None]:
        """Serialize live revalidation and execution under the scripts' shared lock."""
        lock_root = Path(
            os.getenv("SNAP_USER_COMMON")
            or os.getenv("XDG_RUNTIME_DIR")
            or os.getenv("TMPDIR")
            or "/tmp"
        )
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_path = lock_root / f"canonical-ai-lab-assistant-terraform-{os.getuid()}.lock"
        with lock_path.open("a+b") as lock_file:
            self.ui.print_ai_status("Waiting for the infrastructure operation lock...")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            self._infrastructure_lock_fd = lock_file.fileno()
            try:
                yield
            finally:
                self._infrastructure_lock_fd = None
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _execute_approved_plan(self, plan: ExecutionPlan) -> str:
        """Execute one exact approved plan, then return the observation to the AI."""
        self.ui.print_tool_call(plan.action)
        logger.info(
            "Executing approved plan: action=%s plan_id=%s params=%s",
            plan.action,
            plan.short_id,
            plan.parameters,
        )
        tool_result = self._handle_local_tool(plan.action, plan.parameters)
        if tool_result is None:
            tool_result = self._execute_action(plan.action, plan.parameters)
            self._record_deployment(plan.action, plan.parameters, tool_result)

        self._invalidate_host_state()

        if self._is_failed_tool_result(tool_result):
            self.ai_engine.record_tool_observation(plan.action, tool_result)
            return self._compose_failed_tool_response(plan.action, tool_result)

        verification_result = self._verify_mutation_postconditions(plan)
        tool_result_for_ai = self._prepare_tool_result_for_ai(plan.action, tool_result)
        if verification_result:
            tool_result_for_ai = f"{verification_result}\n\n{tool_result_for_ai}"
        self.ui.print_phase("analyzing", "Verifying and explaining the result...")
        ai_response = self.ai_engine.feed_tool_result(plan.action, tool_result_for_ai)
        return self._run_agent_loop(
            f"Approved plan {plan.short_id} completed for {plan.action}",
            ai_response,
        )

    def _build_execution_plan(
        self,
        action: str,
        parameters: dict[str, Any],
        message: str,
        reasoning: str,
    ) -> ExecutionPlan:
        """Normalize an AI tool call into the exact plan shown and later executed."""
        resolved = dict(parameters)
        topology: TopologySpec | None = None
        capacity: CapacitySnapshot | None = None
        environment: EnvironmentSnapshot | None = None

        if action == "deploy_microcloud":
            state = self._collect_host_state(force=True)
            capacity = self._capacity_snapshot(state)
            resolved, topology = self._resolve_deployment_parameters(resolved, capacity)
        elif action in {"add_cluster_node", "scale_environment"}:
            topology, environment = self._resolve_expansion_plan(action, resolved)
            state = self._collect_host_state(force=True)
            capacity = self._capacity_snapshot(state, environment.storage_pool)
            self._bind_state_guards(resolved, environment)
        elif action == "delete_environment":
            workspace = str(resolved.get("workspace", ""))
            environment = self._workspace_snapshot(workspace, target_nodes=0)
            self._bind_state_guards(resolved, environment)

        summary = self._compose_user_facing_response(message, reasoning)
        if not summary:
            summary = f"Execute {action.replace('_', ' ')}"

        return ExecutionPlan(
            action=action,
            parameters=resolved,
            summary=summary,
            topology=topology,
            capacity=capacity,
            environment=environment,
        )

    def _resolve_deployment_parameters(
        self,
        parameters: dict[str, Any],
        capacity: CapacitySnapshot,
    ) -> tuple[dict[str, Any], TopologySpec]:
        """Resolve every deployment resource field before validation and approval."""
        resolved = dict(parameters)
        prefix = str(resolved.get("user_prefix", "lab")).removesuffix("_microcloud")
        workspace = f"{prefix}_microcloud"
        if self.cluster_verifier.workspace_exists(workspace):
            raise ValueError(
                f"Workspace '{workspace}' already exists. Use add/scale for a supported "
                "expansion, or delete it before creating a fresh deployment."
            )

        nodes = int(resolved.get("nodes", 3))
        sizing_tier = str(resolved.get("sizing_tier", "balanced"))
        profile = self._tier_to_profile(sizing_tier)
        sizing_state = {
            "cpu_cores": capacity.cpu_available,
            "ram_total_mb": capacity.ram_available_mb,
            "storage_available_gib": capacity.storage_available_gib,
        }
        recommendation = self.sizing_advisor.host_aware_size(
            host_state=sizing_state,
            nodes=nodes,
            profile=profile,
            residual_capacity=True,
        )

        resolved.setdefault("scenario", "custom")
        resolved.setdefault("user_prefix", "lab")
        resolved["nodes"] = nodes
        resolved.setdefault("sizing_tier", sizing_tier)
        resolved.setdefault("node_cpu", recommendation.node_cpu)
        resolved.setdefault("node_memory_mb", recommendation.node_memory_mb)
        resolved.setdefault("root_disk_gib", recommendation.root_disk_gb)
        resolved.setdefault("ceph_disk_gib", recommendation.ceph_disk_gb)
        resolved.setdefault("ceph_disks_per_node", 1)
        resolved.setdefault("local_disk_gib", 0)

        topology = TopologySpec(
            nodes=resolved["nodes"],
            node_cpu=resolved["node_cpu"],
            node_memory_mb=resolved["node_memory_mb"],
            root_disk_gib=resolved["root_disk_gib"],
            ceph_disk_gib=resolved["ceph_disk_gib"],
            ceph_disks_per_node=resolved["ceph_disks_per_node"],
            local_disk_gib=resolved["local_disk_gib"],
        )
        return resolved, topology

    def _resolve_expansion_topology(
        self,
        action: str,
        parameters: dict[str, Any],
    ) -> TopologySpec:
        """Return only the resource delta for callers that do not need state identity."""
        topology, _environment = self._resolve_expansion_plan(action, parameters)
        return topology

    def _resolve_expansion_plan(
        self,
        action: str,
        parameters: dict[str, Any],
    ) -> tuple[TopologySpec, EnvironmentSnapshot]:
        """Resolve resource delta and exact current-to-target workspace transition."""
        workspace = str(parameters.get("workspace", ""))
        spec = self.cluster_verifier.deployment_spec(workspace)
        if not spec:
            raise ValueError(
                f"Workspace '{workspace}' predates versioned deployment specs. "
                "Back up anything needed, delete that environment, then create it fresh "
                "with this version before using add/scale."
            )

        state = self.cluster_verifier.workspace_state(workspace)
        if not state:
            raise ValueError(f"Workspace '{workspace}' has no readable Terraform state identity")

        current_nodes = int(state.get("current_nodes", 0) or 0)
        spec_nodes = int(spec.get("node_count", 0) or 0)
        if current_nodes != spec_nodes:
            raise ValueError(
                f"Workspace '{workspace}' state is inconsistent: "
                f"node_names={current_nodes}, deployment_spec.node_count={spec_nodes}"
            )
        if action == "add_cluster_node":
            added_nodes = int(parameters.get("add_nodes", 1))
            target_nodes = current_nodes + added_nodes
        else:
            target_nodes = int(parameters.get("target_nodes", 0))
            added_nodes = target_nodes - current_nodes
            if added_nodes <= 0:
                raise ValueError(
                    f"Scale target must be greater than the current {current_nodes} nodes; "
                    "safe downscale is not implemented"
                )

        if current_nodes + added_nodes > 50:
            raise ValueError(
                f"Expansion would create {current_nodes + added_nodes} nodes; "
                "the supported maximum is 50"
            )

        topology = TopologySpec(
            nodes=added_nodes,
            node_cpu=spec["node_cpu"],
            node_memory_mb=spec["node_memory_mb"],
            root_disk_gib=spec["root_disk_gib"],
            ceph_disk_gib=spec["ceph_disk_gib"],
            ceph_disks_per_node=spec["ceph_disks_per_node"],
            local_disk_gib=spec["local_disk_gib"],
        )
        environment = EnvironmentSnapshot(
            workspace=workspace,
            state_lineage=str(state["state_lineage"]),
            state_serial=int(state["state_serial"]),
            current_nodes=current_nodes,
            target_nodes=target_nodes,
            storage_pool=str(spec["lxd_storage_pool"]),
        )
        return topology, environment

    def _workspace_snapshot(self, workspace: str, target_nodes: int) -> EnvironmentSnapshot:
        """Read exact workspace identity for a destructive lifecycle plan."""
        if not self.cluster_verifier.workspace_exists(workspace):
            raise ValueError(f"Workspace '{workspace}' does not exist")
        state = self.cluster_verifier.workspace_state(workspace)
        if not state or not state.get("state_lineage"):
            raise ValueError(f"Workspace '{workspace}' has no readable Terraform state identity")
        return EnvironmentSnapshot(
            workspace=workspace,
            state_lineage=str(state["state_lineage"]),
            state_serial=int(state["state_serial"]),
            current_nodes=int(state["current_nodes"]),
            target_nodes=target_nodes,
            storage_pool=str(state.get("storage_pool", "unknown")),
        )

    @staticmethod
    def _bind_state_guards(
        parameters: dict[str, Any],
        environment: EnvironmentSnapshot,
    ) -> None:
        """Pass approval-bound state identity into the executing script."""
        parameters.update(
            {
                "expected_state_lineage": environment.state_lineage,
                "expected_state_serial": environment.state_serial,
                "expected_current_nodes": environment.current_nodes,
                "expected_target_nodes": environment.target_nodes,
            }
        )

    def _capacity_snapshot(
        self,
        state: dict[str, Any],
        storage_pool: str | None = None,
    ) -> CapacitySnapshot:
        """Calculate conservative residual capacity after active labs and host reserve."""
        cpu_total = int(state.get("cpu_cores", 0) or 0)
        cpu_reserved = max(cpu_total // 5, 2) if cpu_total else 0
        consumed_cpu = int(state.get("consumed_cpu", 0) or 0)
        cpu_available = max(cpu_total - consumed_cpu - cpu_reserved, 0)

        ram_total_mb = int(state.get("ram_total_mb", 0) or 0)
        runtime_available_mb = int(state.get("ram_available_mb", 0) or 0)
        ram_reserved_mb = max(ram_total_mb // 5, 4096) if ram_total_mb else 0
        consumed_ram_mb = int(state.get("consumed_ram_gb", 0) or 0) * 1024
        allocation_available_mb = max(ram_total_mb - consumed_ram_mb - ram_reserved_mb, 0)
        ram_available_mb = min(
            max(runtime_available_mb - ram_reserved_mb, 0),
            allocation_available_mb,
        )

        state_pool = state.get("primary_pool")
        selected_pool = storage_pool or str(state_pool or "default")
        if state_pool is None or selected_pool == state_pool:
            storage_free = int(state.get("storage_available_gib", 0) or 0)
        else:
            storage_free = self._get_pool_available_gib(selected_pool)
        storage_available = max(storage_free - 20, 0)

        return CapacitySnapshot(
            cpu_available=cpu_available,
            ram_available_mb=ram_available_mb,
            storage_available_gib=storage_available,
            storage_pool=selected_pool,
        )

    def _revalidate_approved_plan(self, plan: ExecutionPlan):
        """Re-check identity, geometry, and live capacity immediately before execution."""
        if plan.environment is not None:
            try:
                current_environment = self._workspace_snapshot(
                    plan.environment.workspace,
                    target_nodes=plan.environment.target_nodes,
                )
            except ValueError as exc:
                return PlanValidation(valid=False, errors=(str(exc),))
            if current_environment != plan.environment:
                return PlanValidation(
                    valid=False,
                    errors=(
                        "The target workspace state identity or node count changed after "
                        "this plan was prepared; prepare and approve a new plan.",
                    ),
                )

        if plan.topology is None:
            return self.plan_validator.validate(plan)

        if plan.action == "deploy_microcloud":
            prefix = str(plan.parameters.get("user_prefix", "lab")).removesuffix("_microcloud")
            workspace = f"{prefix}_microcloud"
            if self.cluster_verifier.workspace_exists(workspace):
                return PlanValidation(
                    valid=False,
                    errors=(f"Workspace '{workspace}' was created after this plan was prepared.",),
                )

        if plan.action in {"add_cluster_node", "scale_environment"}:
            try:
                current_topology, current_environment = self._resolve_expansion_plan(
                    plan.action,
                    plan.parameters,
                )
            except ValueError as exc:
                return PlanValidation(valid=False, errors=(str(exc),))
            if current_topology != plan.topology or current_environment != plan.environment:
                return PlanValidation(
                    valid=False,
                    errors=(
                        "The target workspace geometry changed after this plan was prepared; "
                        "prepare and approve a new plan.",
                    ),
                )

        current_state = self._collect_host_state(force=True)
        storage_pool = plan.capacity.storage_pool if plan.capacity else None
        current_plan = plan.model_copy(
            update={"capacity": self._capacity_snapshot(current_state, storage_pool)}
        )
        return self.plan_validator.validate(current_plan)

    def _format_plan_for_confirmation(self, plan: ExecutionPlan) -> str:
        """Render the exact immutable plan the user is being asked to approve."""
        lines = [plan.summary, "", f"Plan ID: {plan.short_id}", f"Action: {plan.action}"]
        workspace = str(plan.parameters.get("workspace", "")).strip()
        if plan.action == "deploy_microcloud":
            prefix = str(plan.parameters.get("user_prefix", "lab")).removesuffix("_microcloud")
            workspace = f"{prefix}_microcloud"
        if workspace:
            lines.append(f"Environment: {workspace}")
        if plan.environment is not None:
            environment = plan.environment
            lines.extend(
                [
                    "State identity: "
                    f"lineage={environment.state_lineage[:12]}... / "
                    f"serial={environment.state_serial}",
                    f"Node transition: {environment.current_nodes} -> {environment.target_nodes}",
                    f"Storage pool: {environment.storage_pool}",
                ]
            )

        if plan.topology is not None:
            topology = plan.topology
            node_label = "Nodes"
            if plan.action in {"add_cluster_node", "scale_environment"}:
                node_label = "New nodes (resource delta)"
            lines.extend(
                [
                    f"{node_label}: {topology.nodes}",
                    "Per node: "
                    f"{topology.node_cpu} vCPU / {topology.node_memory_mb // 1024} GiB RAM / "
                    f"{topology.root_disk_gib} GiB root",
                    "Storage per node: "
                    f"{topology.ceph_disks_per_node} x {topology.ceph_disk_gib} GiB Ceph / "
                    f"{topology.local_disk_gib} GiB local",
                    "Totals: "
                    f"{topology.total_cpu} vCPU / {topology.total_ram_mb // 1024} GiB RAM / "
                    f"{topology.total_storage_gib} GiB storage",
                ]
            )
        if plan.capacity is not None and plan.environment is None:
            lines.append(f"Storage pool: {plan.capacity.storage_pool}")
        visible_params = ", ".join(
            f"{key}={value}" for key, value in sorted(plan.parameters.items())
        )
        if visible_params:
            lines.append(f"Exact parameters: {visible_params}")
        lines.extend(["", "Approve this exact plan?"])
        return "\n".join(lines)

    @staticmethod
    def _format_plan_validation_failure(errors: tuple[str, ...]) -> str:
        details = "\n".join(f"- {error}" for error in errors)
        return f"Error: the proposed plan was rejected by deterministic validation:\n{details}"

    def _invalidate_host_state(self) -> None:
        self._host_state_cache = None
        self._host_state_cache_ts = 0.0

    def _verify_mutation_postconditions(self, plan: ExecutionPlan) -> str:
        """Return structured evidence after operations that should form a cluster."""
        if plan.action not in {
            "deploy_microcloud",
            "add_cluster_node",
            "scale_environment",
        }:
            return ""

        if plan.action == "deploy_microcloud":
            prefix = str(plan.parameters.get("user_prefix", "lab")).removesuffix("_microcloud")
            workspace = f"{prefix}_microcloud"
        else:
            workspace = str(plan.parameters.get("workspace", ""))

        expected_nodes = None
        if plan.action == "deploy_microcloud" and plan.topology is not None:
            expected_nodes = plan.topology.nodes
        if plan.environment is not None:
            expected_nodes = plan.environment.target_nodes
        expected_osds = None
        if expected_nodes is not None and plan.topology is not None:
            expected_osds = expected_nodes * plan.topology.ceph_disks_per_node
        report = self.cluster_verifier.verify(
            workspace,
            expected_nodes=expected_nodes,
            expected_osds=expected_osds,
        )
        return (
            f"POSTCONDITION STATUS: {report.status}\n"
            "The script finished, but only this report determines whether the "
            "requested cluster state was achieved.\n"
            f"{report.as_tool_result()}"
        )

    def _refresh_ai_environment_context(self) -> None:
        """Push a fresh host-grounded context snapshot into the AI engine.

        Failures here must never block the chat, so we degrade silently.
        """
        try:
            state = self._collect_host_state()
            self.ai_engine.set_environment_context(self._format_host_state_for_prompt(state))
        except Exception as exc:
            logger.debug(f"Could not refresh environment context: {exc}")

    def _is_failed_tool_result(self, tool_result: str) -> bool:
        """Detect hard failures from script-backed tools."""
        if not tool_result:
            return False
        return (
            tool_result.startswith("Script failed")
            or tool_result.startswith("Error:")
            or "Traceback" in tool_result
        )

    def _compose_failed_tool_response(self, action: str, tool_result: str) -> str:
        """Return a deterministic failure summary so we never imply background work continues."""
        summary = self._truncate_text(tool_result.strip(), 1200)
        if action in (
            "deploy_microcloud",
            "delete_environment",
            "scale_environment",
            "add_cluster_node",
            "verify_cluster_health",
        ):
            return (
                f"{action.replace('_', ' ').capitalize()} failed. No background work is running.\n\n"
                f"Last error:\n{summary}"
            )
        return f"Operation failed.\n\nLast error:\n{summary}"

    def _resolve_tool_action(
        self,
        action: str | None,
        message: str,
        reasoning: str,
        parameters: dict[str, Any],
        user_input: str,
    ) -> str | None:
        """Normalize generic tool labels back to a concrete tool name when possible."""
        known_tools = {
            tool["name"] for tool in get_tool_definitions().get("tools", []) if tool.get("name")
        }
        if action in known_tools:
            return action

        text = "\n".join(part for part in (user_input, message, reasoning) if part).lower()

        if any(
            keyword in text for keyword in ("delete", "cleanup", "clean up", "destroy", "remove")
        ):
            return "delete_environment"
        if any(keyword in text for keyword in ("list", "show", "enumerate")) and any(
            keyword in text for keyword in ("lab", "environment", "environments", "labs")
        ):
            return "list_environments"
        if any(keyword in text for keyword in ("health", "status", "check", "verify")):
            return "verify_cluster_health"
        if any(
            keyword in text
            for keyword in ("add node", "add nodes", "join node", "join nodes", "expand cluster")
        ):
            return "add_cluster_node"
        if any(
            keyword in text
            for keyword in (
                "scale",
                "resize",
                "increase nodes",
                "more nodes",
                "re-deploy",
                "redeploy",
            )
        ):
            return "scale_environment"

        workspace_hint = parameters.get("workspace") if isinstance(parameters, dict) else None
        if action == "tool_call" and isinstance(workspace_hint, str) and workspace_hint:
            return "delete_environment"

        return action if action in known_tools else None

    def _compose_user_facing_response(self, message: str, reasoning: str) -> str:
        """Build a concise, readable assistant response for terminal UX."""
        clean_message = self._normalize_assistant_text(message)
        clean_reasoning = self._normalize_assistant_text(reasoning)

        if clean_message:
            return clean_message

        if clean_reasoning:
            # Keep reasoning as a short fallback when message is missing.
            short_reasoning = self._truncate_text(clean_reasoning, 320)
            return f"Plan: {short_reasoning}"

        return "I received an empty response from the inference backend. Please retry your request."

    def _normalize_assistant_text(self, text: str) -> str:
        """Remove noisy fragments and repeated blocks from model output."""
        if not text:
            return ""

        cleaned = text.replace("\r\n", "\n").strip()
        cleaned = re.sub(r"<tool_code>\s*\{.*?\}\s*</tool_code>", "", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
        cleaned = cleaned.replace("```", "")
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        # Remove immediate duplicate paragraphs that frequently appear with local models.
        blocks = [b.strip() for b in re.split(r"\n\s*\n", cleaned) if b.strip()]
        deduped: list[str] = []
        seen_recent: list[str] = []
        for block in blocks:
            key = re.sub(r"\s+", " ", block.lower()).strip()
            if key in seen_recent:
                continue
            deduped.append(block)
            seen_recent.append(key)
            if len(seen_recent) > 12:
                # Keep memory bounded and focus on near-duplicates.
                seen_recent = seen_recent[-8:]

        return "\n\n".join(deduped).strip()

    def _format_for_terminal(self, text: str) -> str:
        """Render readable terminal output with wrapped text and aligned tables."""
        if not text:
            return ""

        rendered = self._render_markdown_tables(text)

        output_lines: list[str] = []
        in_table = False
        for raw_line in rendered.splitlines():
            line = self._strip_markdown_noise(raw_line.rstrip())
            if line.startswith("+") and line.endswith("+"):
                in_table = True
                output_lines.append(line)
                continue
            if in_table and line.startswith("|") and line.endswith("|"):
                output_lines.append(line)
                continue
            if in_table and line.strip() == "":
                in_table = False
                output_lines.append("")
                continue
            if in_table and not (line.startswith("|") and line.endswith("|")):
                in_table = False

            if not line:
                output_lines.append("")
                continue

            if self._is_list_line(line):
                output_lines.append(textwrap.fill(line, width=UI_WIDTH, subsequent_indent="  "))
            else:
                output_lines.append(textwrap.fill(line, width=UI_WIDTH))

        compacted = self._compact_blank_lines(output_lines)
        return "\n".join(compacted).strip()

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    def _print_assistant_response(self, response: str) -> None:
        """Print the final assistant response with the rich UI."""
        if not response:
            self.ui.print_ai_response("")
            return

        # Handle confirmation flow
        if response.startswith("__CONFIRM__:"):
            parts = response[len("__CONFIRM__:") :].rsplit("\n\n", 1)
            message = parts[0] if len(parts) > 1 else ""
            prompt = parts[-1] if parts else "Shall I proceed?"
            self.ui.print_confirmation_prompt(message, prompt)
            return

        cleaned = self._normalize_assistant_text(response)
        if not cleaned:
            self.ui.print_ai_response("")
            return

        cleaned = self._truncate_for_display(cleaned)
        formatted = self._format_for_terminal(cleaned)
        self.ui.print_ai_response(formatted)

    def _print_section_header(self, title: str) -> None:
        """Legacy header — now delegates to UI phase display."""
        self.ui.print_phase("executing", title)

    def _print_section_footer(self) -> None:
        pass

    def _strip_markdown_noise(self, line: str) -> str:
        """Remove visual markdown artifacts that hurt terminal readability."""
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = line.replace("**", "")
        line = line.replace("__", "")
        line = line.replace("`", "")
        return line

    def _is_list_line(self, line: str) -> bool:
        stripped = line.lstrip()
        return bool(re.match(r"^(-|\*|\d+\.)\s+", stripped))

    def _compact_blank_lines(self, lines: list[str]) -> list[str]:
        compacted: list[str] = []
        blank_streak = 0
        for line in lines:
            if line.strip() == "":
                blank_streak += 1
                if blank_streak > 1:
                    continue
            else:
                blank_streak = 0
            compacted.append(line)
        return compacted

    def _truncate_for_display(self, text: str) -> str:
        """Keep terminal output scannable by trimming very long responses."""
        max_lines = 36
        lines = text.splitlines()
        if len(lines) <= max_lines:
            return text

        kept = lines[:max_lines]
        kept.append("")
        kept.append("[Output shortened for readability. Ask: 'give me full details' if needed.]")
        return "\n".join(kept)

    def _render_markdown_tables(self, text: str) -> str:
        """Convert markdown tables to aligned ASCII tables for terminal display."""
        lines = text.splitlines()
        rendered: list[str] = []
        idx = 0

        while idx < len(lines):
            if "|" not in lines[idx]:
                rendered.append(lines[idx])
                idx += 1
                continue

            start = idx
            while idx < len(lines) and "|" in lines[idx]:
                idx += 1

            block = lines[start:idx]
            if self._looks_like_markdown_table(block):
                rendered.append(self._render_markdown_table_block(block))
            else:
                rendered.extend(block)

        return "\n".join(rendered)

    def _looks_like_markdown_table(self, lines: list[str]) -> bool:
        if len(lines) < 2:
            return False
        separator = lines[1].strip()
        return bool(re.match(r"^\|?\s*[:\-\|\s]+\|?\s*$", separator))

    def _render_markdown_table_block(self, lines: list[str]) -> str:
        rows: list[list[str]] = []
        for line in lines:
            if re.match(r"^\|?\s*[:\-\|\s]+\|?\s*$", line.strip()):
                continue
            stripped = line.strip().strip("|")
            cells = [self._strip_markdown_noise(cell.strip()) for cell in stripped.split("|")]
            rows.append(cells)

        if not rows:
            return ""

        col_count = max(len(r) for r in rows)
        for row in rows:
            if len(row) < col_count:
                row.extend([""] * (col_count - len(row)))

        widths = [max(len(row[col]) for row in rows) for col in range(col_count)]

        def render_row(row: list[str]) -> str:
            cols = [f" {row[i].ljust(widths[i])} " for i in range(col_count)]
            return "|" + "|".join(cols) + "|"

        sep = "+" + "+".join(["-" * (w + 2) for w in widths]) + "+"
        output = [sep, render_row(rows[0]), sep]
        for row in rows[1:]:
            output.append(render_row(row))
        output.append(sep)
        return "\n".join(output)

    def _handle_local_tool(self, action: str, parameters: dict[str, Any]) -> str | None:
        """Execute tools that do not require external scripts."""
        if action == "inspect_host_environment":
            return self._inspect_host_environment()

        if action == "verify_cluster_health":
            workspace = str(parameters.get("workspace", ""))
            report = self.cluster_verifier.verify(workspace)
            return f"POSTCONDITION STATUS: {report.status}\n{report.as_tool_result()}"

        if action == "propose_custom_topology":
            nodes = int(parameters.get("node_count", 3))
            cpu = int(parameters.get("node_cpu", 2))
            ram = int(parameters.get("node_ram_gb", 8))
            root = int(parameters.get("root_disk_gb", 40))
            ceph = int(parameters.get("ceph_disk_gb", 50))
            reasoning = parameters.get("reasoning", "")
            trade_offs = parameters.get("trade_offs", "")
            alternative = parameters.get("alternative", "")

            state = self._collect_host_state()
            capacity = self._capacity_snapshot(state)
            topology = TopologySpec(
                nodes=nodes,
                node_cpu=cpu,
                node_memory_mb=ram * 1024,
                root_disk_gib=root,
                ceph_disk_gib=ceph,
                ceph_disks_per_node=1,
                local_disk_gib=0,
            )
            proposal_plan = ExecutionPlan(
                action="deploy_microcloud",
                parameters={"nodes": nodes},
                summary="Validate topology proposal",
                topology=topology,
                capacity=capacity,
            )
            validation = self.plan_validator.validate(proposal_plan)
            if not validation.valid:
                return self._format_plan_validation_failure(validation.errors)

            total_cpu = nodes * cpu
            total_ram = nodes * ram
            total_ceph_raw = nodes * ceph
            total_ceph_usable = int(total_ceph_raw / 3)

            lines = [
                "Custom topology proposal",
                "",
                f"  Nodes            : {nodes}",
                f"  vCPU / node      : {cpu} (total: {total_cpu} vCPU)",
                f"  RAM / node       : {ram} GB (total: {total_ram} GB)",
                f"  Root disk / node : {root} GB",
                f"  Ceph disk / node : {ceph} GB",
                f"  Ceph capacity    : ~{total_ceph_usable} GB usable ({total_ceph_raw} GB raw, estimated 3x replication)",
                "  OVN networking   : enabled (required by this automation)",
            ]
            if reasoning:
                lines += ["", "Why:", f"  {reasoning}"]
            if trade_offs:
                lines += ["", "Trade-offs:", f"  {trade_offs}"]
            if alternative:
                lines += ["", "Alternative:", f"  {alternative}"]
            return "\n".join(lines)

        if action == "get_sizing_recommendation":
            scenario_name = parameters.get("scenario", "custom")
            nodes_value = parameters.get("nodes")
            requested_nodes = int(nodes_value) if nodes_value is not None else None
            workload = parameters.get("workload_description", "")
            tier_str = parameters.get("tier")
            tier = SizingTier(tier_str) if tier_str else None

            # Prefer host-aware sizing: it mirrors deploy_microcloud.sh exactly, so
            # the numbers shown to the user are what will actually be provisioned.
            host_state = self._collect_host_state()
            if host_state.get("cpu_cores"):
                profile = self._tier_to_profile(tier_str, workload)
                capacity = self._capacity_snapshot(host_state)
                host_sizing = self.sizing_advisor.host_aware_size(
                    host_state={
                        "cpu_cores": capacity.cpu_available,
                        "ram_total_mb": capacity.ram_available_mb,
                        "storage_available_gib": capacity.storage_available_gib,
                    },
                    nodes=requested_nodes or 3,
                    profile=profile,
                    residual_capacity=True,
                )
                return host_sizing.summary()

            rec = self.sizing_advisor.recommend(
                scenario_name=scenario_name,
                nodes=requested_nodes,
                workload_description=workload,
                override_tier=tier,
            )
            return rec.summary()

        if action == "get_documentation":
            topic = parameters.get("topic", "microcloud")
            url = parameters.get("url")
            if url:
                doc = self.doc_fetcher.fetch_by_url(url)
            else:
                doc = self.doc_fetcher.fetch_by_topic(topic)
            if doc.get("error"):
                return f"Could not fetch documentation: {doc['error']}"
            title = doc.get("title", topic)
            content = doc.get("content", "")
            fetched_at = doc.get("fetched_at", "unknown")
            return (
                f"Doc: {title}\nSource: {doc['url']}\nRetrieved: {fetched_at}\n\n{content[:6000]}"
            )

        return None

    def _inspect_host_environment(self) -> str:
        """Collect host facts for grounded planning decisions."""
        state = self._collect_host_state(force=True)
        return self._format_host_state_report(state)

    def _run_host_cmd(self, cmd: str, timeout: int = 10) -> str:
        """Run a read-only host probe and return trimmed stdout (or '' on failure)."""
        try:
            res = subprocess.run(
                ["bash", "-lc", cmd],
                capture_output=True,
                text=True,
                cwd=self.config.repo_root,
                timeout=timeout,
            )
            return (res.stdout or "").strip()
        except Exception:
            return ""

    def _collect_host_state(self, force: bool = False) -> dict[str, Any]:
        """Collect rich, structured host facts used for AI grounding.

        Cached for a short TTL so we can inject it into the prompt every turn
        without repeatedly shelling out. Set force=True for an explicit refresh
        (e.g. the inspect_host_environment tool).
        """
        now = time.time()
        if (
            not force
            and self._host_state_cache is not None
            and (now - self._host_state_cache_ts) < self._host_state_ttl
        ):
            return self._host_state_cache

        cpu_cores = self._parse_int(self._run_host_cmd("nproc 2>/dev/null"), default=0)
        ram_total_mb = self._parse_int(
            self._run_host_cmd("awk '/MemTotal:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null"),
            default=0,
        )
        ram_available_mb = self._parse_int(
            self._run_host_cmd(
                "awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null"
            ),
            default=0,
        )
        disks = self._run_host_cmd(
            "lsblk -dn -o NAME,SIZE,TYPE | awk '$3==\"disk\" {print $1\":\"$2}' | paste -sd ',' -"
        )
        networks_raw = self._run_host_cmd(
            "lxc network list --format csv 2>/dev/null | awk -F',' '{print $1}' | paste -sd ',' -"
        )
        pools_raw = self._run_host_cmd(
            "lxc storage list --format csv 2>/dev/null | awk -F',' '{print $1}' | paste -sd ',' -"
        )
        lxd_version = self._run_host_cmd(
            "lxc version 2>/dev/null | awk '/Server version:/ {print $3; exit}'"
        )

        pools = [p.strip() for p in pools_raw.split(",") if p.strip()] if pools_raw else []
        primary_pool = "default" if "default" in pools else (pools[0] if pools else "default")
        storage_available_gib = self._get_pool_available_gib(primary_pool)
        environments = self._collect_environment_usage()

        consumed_cpu = sum(env.get("total_cpu", 0) for env in environments)
        consumed_ram_gb = sum(env.get("total_ram_gb", 0) for env in environments)

        state: dict[str, Any] = {
            "cpu_cores": cpu_cores,
            "ram_total_mb": ram_total_mb,
            "ram_available_mb": ram_available_mb,
            "disks": disks or "unknown",
            "lxd_networks": networks_raw or "unknown",
            "lxd_storage_pools": pools_raw or "unknown",
            "primary_pool": primary_pool,
            "storage_available_gib": storage_available_gib,
            "lxd_version": lxd_version or "unknown",
            "environments": environments,
            "consumed_cpu": consumed_cpu,
            "consumed_ram_gb": consumed_ram_gb,
        }

        self._host_state_cache = state
        self._host_state_cache_ts = now
        return state

    def _get_pool_available_gib(self, pool: str) -> int:
        """Best-effort free space (GiB) in an LXD storage pool, with df fallback."""
        info = self._run_host_cmd(f"lxc storage info {pool} 2>/dev/null")
        line = ""
        for raw in info.splitlines():
            if "Space available:" in raw:
                line = raw.split("Space available:", 1)[1].strip()
                break
        if line:
            num_match = re.search(r"[0-9]+(?:\.[0-9]+)?", line)
            unit_match = re.search(r"[A-Za-z]+", line)
            if num_match:
                num = float(num_match.group(0))
                unit = (unit_match.group(0) if unit_match else "GiB").upper()
                if unit.startswith("T"):
                    return int(num * 1024)
                if unit.startswith("G"):
                    return int(num)
                if unit.startswith("M"):
                    return int(num / 1024)
        df_out = self._run_host_cmd(
            "df -BG . 2>/dev/null | awk 'NR==2 {gsub(/G/,\"\",$4); print $4}'"
        )
        return self._parse_int(df_out, default=0)

    def _collect_environment_usage(self) -> list[dict[str, Any]]:
        """List active lab environments and their resource footprint via lxc.

        Lab VMs follow the naming pattern '<prefix>-node-<n>'. We group by prefix,
        count nodes, and sum CPU/RAM limits so the AI knows real free headroom.
        """
        csv_out = self._run_host_cmd(
            "lxc list --format csv -c n,s,config:limits.cpu,config:limits.memory 2>/dev/null"
        )
        if not csv_out:
            return []

        groups: dict[str, dict[str, Any]] = {}
        for row in csv_out.splitlines():
            cols = [c.strip() for c in row.split(",")]
            if not cols or not cols[0]:
                continue
            name = cols[0]
            match = re.match(r"^(?P<prefix>.+)-node-\d+$", name)
            if not match:
                continue
            prefix = match.group("prefix")
            cpu = self._parse_int(cols[2] if len(cols) > 2 else "", default=0)
            ram_gb = self._parse_memory_gb(cols[3] if len(cols) > 3 else "")

            env = groups.setdefault(
                prefix,
                {
                    "name": prefix,
                    "nodes": 0,
                    "node_cpu": cpu,
                    "node_ram_gb": ram_gb,
                    "total_cpu": 0,
                    "total_ram_gb": 0,
                },
            )
            env["nodes"] += 1
            env["total_cpu"] += cpu
            env["total_ram_gb"] += ram_gb

        return list(groups.values())

    @staticmethod
    def _parse_int(value: str, default: int = 0) -> int:
        match = re.search(r"-?\d+", value or "")
        return int(match.group(0)) if match else default

    @staticmethod
    def _parse_memory_gb(value: str) -> int:
        """Parse an LXD memory limit like '8GiB' / '8192MiB' into whole GB."""
        if not value:
            return 0
        num_match = re.search(r"[0-9]+(?:\.[0-9]+)?", value)
        if not num_match:
            return 0
        num = float(num_match.group(0))
        unit_match = re.search(r"[A-Za-z]+", value)
        unit = (unit_match.group(0) if unit_match else "GiB").upper()
        if unit.startswith("T"):
            return int(num * 1024)
        if unit.startswith("M"):
            return int(num / 1024)
        if unit.startswith("K"):
            return int(num / (1024 * 1024))
        return int(num)

    @staticmethod
    def _tier_to_profile(tier_str: str | None, workload: str = "") -> str:
        """Map a requested tier/workload to a host-aware sizing profile."""
        text = f"{tier_str or ''} {workload or ''}".lower()
        if any(
            k in text
            for k in ("minimal", "poc", "proof of concept", "dev", "sandbox", "conservative")
        ):
            return "conservative"
        if any(
            k in text
            for k in (
                "large",
                "production",
                "prod",
                "performance",
                "ha",
                "high availability",
                "enterprise",
            )
        ):
            return "performance"
        return "balanced"

    def _format_host_state_report(self, state: dict[str, Any]) -> str:
        """Human-readable host snapshot returned by inspect_host_environment."""
        envs = state.get("environments", [])
        if envs:
            env_lines = "\n".join(
                f"    - {e['name']}: {e['nodes']} nodes "
                f"({e['node_cpu']} vCPU / {e['node_ram_gb']} GB each, "
                f"total {e['total_cpu']} vCPU / {e['total_ram_gb']} GB)"
                for e in envs
            )
        else:
            env_lines = "    (none)"

        ram_total_gb = round(state.get("ram_total_mb", 0) / 1024, 1)
        ram_avail_gb = round(state.get("ram_available_mb", 0) / 1024, 1)
        return (
            "Host environment snapshot\n"
            "  Deployment mode : nested-lxd-lab (OpenTofu creates MicroCloud VMs)\n"
            "  Ceph disk model : per-node virtual block volumes are provisioned automatically\n"
            f"  CPU cores        : {state.get('cpu_cores', 'unknown')}\n"
            f"  RAM              : {ram_total_gb} GB total ({ram_avail_gb} GB available)\n"
            f"  Disk devices     : {state.get('disks', 'unknown')}\n"
            f"  LXD version      : {state.get('lxd_version', 'unknown')}\n"
            f"  LXD networks     : {state.get('lxd_networks', 'unknown')}\n"
            f"  LXD storage pool : {state.get('lxd_storage_pools', 'unknown')} "
            f"(~{state.get('storage_available_gib', 0)} GiB free in '{state.get('primary_pool', 'default')}')\n"
            "  Active lab environments:\n"
            f"{env_lines}"
        )

    def _format_host_state_for_prompt(self, state: dict[str, Any]) -> str:
        """Compact, capacity-focused context injected into the AI system prompt."""
        cpu = state.get("cpu_cores", 0)
        ram_total_gb = round(state.get("ram_total_mb", 0) / 1024)
        ram_avail_gb = round(state.get("ram_available_mb", 0) / 1024)
        pool = state.get("primary_pool", "default")
        free_storage = state.get("storage_available_gib", 0)
        consumed_cpu = state.get("consumed_cpu", 0)
        free_cpu = max(cpu - consumed_cpu, 0)
        free_ram = max(ram_avail_gb, 0)

        envs = state.get("environments", [])
        if envs:
            env_lines = "\n".join(
                f"    - {e['name']}: {e['nodes']} nodes "
                f"(~{e['total_cpu']} vCPU / {e['total_ram_gb']} GB)"
                for e in envs
            )
        else:
            env_lines = "    (none)"

        return (
            f"  Host capacity : {cpu} vCPU | {ram_total_gb} GB RAM total "
            f"({ram_avail_gb} GB available) | {free_storage} GiB free in pool '{pool}'\n"
            f"  LXD version   : {state.get('lxd_version', 'unknown')}\n"
            f"  LXD networks  : {state.get('lxd_networks', 'unknown')}\n"
            f"  Active lab environments (already consuming resources):\n"
            f"{env_lines}\n"
            f"  Approx free headroom for new labs: ~{free_cpu} vCPU / ~{free_ram} GB RAM / "
            f"~{free_storage} GiB storage"
        )

    # Whitelist of parameters each script actually accepts.
    # Any parameter NOT in this map will be silently dropped to prevent
    # the AI from hallucinating options and crashing the scripts.
    _SCRIPT_ACCEPTED_PARAMS: dict[str, set[str]] = {
        "prep_host": set(),
        "install_inference_snap": {"engine"},
        "deploy_microcloud": {
            "scenario",
            "nodes",
            "sizing_tier",
            "node_cpu",
            "node_memory_mb",
            "root_disk_gib",
            "ceph_disk_gib",
            "ceph_disks_per_node",
            "local_disk_gib",
            "user_prefix",
            "ssh_key",
        },
        "delete_environment": {
            "workspace",
            "expected_state_lineage",
            "expected_state_serial",
            "expected_current_nodes",
            "expected_target_nodes",
        },
        "list_environments": set(),
        "scale_environment": {
            "workspace",
            "target_nodes",
            "expected_state_lineage",
            "expected_state_serial",
            "expected_current_nodes",
            "expected_target_nodes",
        },
        "add_cluster_node": {
            "workspace",
            "add_nodes",
            "expected_state_lineage",
            "expected_state_serial",
            "expected_current_nodes",
            "expected_target_nodes",
        },
        "verify_cluster_health": {"workspace"},
    }

    def _execute_action(self, action: str, parameters: dict[str, Any]) -> str:
        """Execute script-backed actions."""
        try:
            script_map = {
                "prep_host": self.config.prep_host_script,
                "install_inference_snap": self.config.install_inference_script,
                "deploy_microcloud": self.config.deploy_microcloud_script,
                "delete_environment": self.config.cleanup_microcloud_script,
                "list_environments": self.config.list_environments_script,
                "scale_environment": self.config.scale_microcloud_script,
                "add_cluster_node": self.config.add_cluster_node_script,
                "verify_cluster_health": self.config.verify_cluster_health_script,
            }

            script_path = script_map.get(action)
            if not script_path:
                return f"No script mapped for action: {action}"

            accepted = self._SCRIPT_ACCEPTED_PARAMS.get(action, set())
            cmd = ["bash", str(script_path)]
            for key, value in parameters.items():
                if value is None or key not in accepted:
                    continue
                # Guard: model sometimes passes full workspace name as user_prefix.
                # deploy_microcloud.sh appends _microcloud itself, so strip it.
                if key == "user_prefix" and isinstance(value, str):
                    value = value.removesuffix("_microcloud")
                cmd.append(f"--{key.replace('_', '-')}={value}")

            if action in (
                "deploy_microcloud",
                "delete_environment",
                "scale_environment",
                "add_cluster_node",
            ):
                cmd.append("--auto-approve")

            logger.info(f"Running: {' '.join(cmd)}")
            capture_only = action in (
                "deploy_microcloud",
                "delete_environment",
                "scale_environment",
                "add_cluster_node",
                "verify_cluster_health",
            )
            output_lines: list[str] = []
            status_label = {
                "deploy_microcloud": "Deployment",
                "delete_environment": "Cleanup",
                "scale_environment": "Scaling",
                "add_cluster_node": "Node Addition",
                "verify_cluster_health": "Health Check",
            }.get(action, "Operation")

            if capture_only:
                self.ui.print_operation_progress(status_label, "Started — streaming checkpoints...")

            operation_started = time.monotonic()
            operation_timed_out = False
            child_env = os.environ.copy()
            inherited_fds: tuple[int, ...] = ()
            if self._infrastructure_lock_fd is not None:
                child_env["LAB_AI_TERRAFORM_LOCK_FD"] = str(self._infrastructure_lock_fd)
                inherited_fds = (self._infrastructure_lock_fd,)
            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.config.repo_root,
                start_new_session=True,
                env=child_env,
                pass_fds=inherited_fds,
            ) as proc:
                assert proc.stdout is not None
                output_fd = proc.stdout.fileno()
                os.set_blocking(output_fd, False)
                milestone_pattern = re.compile(
                    r"\[INFO\]|\[WARN\]|\[ERROR\]|\[SUCCESS\]|"
                    r"Apply complete|Destroy complete|PLAY RECAP|TASK \[",
                    flags=re.IGNORECASE,
                )

                while True:
                    ready, _, _ = select.select([output_fd], [], [], 0.5)
                    if ready:
                        try:
                            chunk = os.read(output_fd, 65536)
                        except BlockingIOError:
                            chunk = b""
                        if chunk:
                            text_chunk = chunk.decode("utf-8", errors="replace")
                            output_lines.append(text_chunk)
                            if capture_only:
                                for line in text_chunk.splitlines():
                                    if milestone_pattern.search(line):
                                        self.ui.print_operation_progress(
                                            status_label,
                                            line.strip(),
                                        )
                            else:
                                print(text_chunk, end="", flush=True)

                    if time.monotonic() - operation_started > self.config.operation_timeout:
                        operation_timed_out = True
                        self._terminate_process_group(proc)
                        break

                    if proc.poll() is not None:
                        break

                while True:
                    try:
                        tail = os.read(output_fd, 65536)
                    except BlockingIOError:
                        break
                    if not tail:
                        break
                    output_lines.append(tail.decode("utf-8", errors="replace"))

                proc.wait()
            if capture_only:
                self.ui.print_phase("done", f"{status_label} finished")

            output = "".join(output_lines)
            if operation_timed_out:
                return (
                    f"Error: {status_label} timed out after "
                    f"{self.config.operation_timeout} seconds.\n{output[-2000:]}"
                )
            if proc.returncode != 0:
                return f"Script failed (exit {proc.returncode}):\n{output[-2000:]}"
            return output or "(no output)"

        except Exception as exc:
            logger.error(f"Action execution error: {exc}")
            return f"Error: {exc}"

    @staticmethod
    def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
        """Terminate an operation and every child process it started."""
        group_id = proc.pid
        try:
            os.killpg(group_id, signal.SIGTERM)
        except ProcessLookupError:
            return

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.killpg(group_id, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            try:
                os.killpg(group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass

        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _run_script(self, script_path) -> str:
        result = subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
            timeout=self.config.response_timeout,
            cwd=self.config.repo_root,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)
        return result.stdout.strip()

    def _record_deployment(self, action, parameters, result):
        record = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "parameters": parameters,
            "success": not self._is_failed_tool_result(result),
            "result_preview": result[:200],
        }
        self.deployment_history.append(record)
        self._save_history()

    def _prepare_tool_result_for_ai(self, action: str, tool_result: str) -> str:
        """Trim very large tool outputs before feeding them back to the model."""
        max_chars = 6000
        deploy_tail_chars = 2000

        if not tool_result:
            return "(no output)"

        if action in ("deploy_microcloud", "scale_environment"):
            access_details = self._extract_deployment_access_details(tool_result)
            if len(tool_result) > deploy_tail_chars:
                compact = (
                    "Operation finished. Full logs were streamed to the terminal and omitted for context safety.\n"
                    f"Tail ({deploy_tail_chars} chars):\n{tool_result[-deploy_tail_chars:]}"
                )
                if access_details:
                    return f"{access_details}\n\n{compact}"
                return compact
            if access_details:
                return f"{access_details}\n\n{tool_result}"

        if len(tool_result) > max_chars:
            head_chars = max_chars // 2
            tail_chars = max_chars - head_chars
            return (
                f"Tool output truncated for context safety (original length: {len(tool_result)} chars).\n"
                f"Head ({head_chars} chars):\n{tool_result[:head_chars]}\n\n"
                f"Tail ({tail_chars} chars):\n{tool_result[-tail_chars:]}"
            )

        return tool_result

    def _extract_deployment_access_details(self, tool_result: str) -> str:
        """Extract deterministic access details (IPs/UI/commands) from deploy script output."""
        if not tool_result:
            return ""

        env_match = re.search(r"^\s*Environment\s+(\S+)\s*$", tool_result, flags=re.MULTILINE)
        workspace = env_match.group(1) if env_match else ""

        node_rows = re.findall(
            r"^\s*(\S+-node-\d+)\s+(\d+\.\d+\.\d+\.\d+|N/A)\s+(https?://\S+|-)\s*$",
            tool_result,
            flags=re.MULTILINE,
        )

        if not workspace and not node_rows:
            return ""

        lines = ["Deployment access details:"]
        if workspace:
            lines.append(f"- Workspace: {workspace}")

        if node_rows:
            lines.append("- Nodes:")
            for node_name, ip, ui_url in node_rows:
                node_bits = [f"{node_name}"]
                node_bits.append(f"IP={ip}")
                if ui_url != "-":
                    node_bits.append(f"UI={ui_url}")
                lines.append(f"  - {', '.join(node_bits)}")

            first_node, first_ip, _ = node_rows[0]
            lines.append("- Quick access:")
            lines.append(f"  - LXD shell: lxc exec {first_node} -- bash")
            if first_ip != "N/A":
                lines.append(f"  - SSH: ssh ubuntu@{first_ip}")

        return "\n".join(lines)

    def _load_history(self):
        if self.config.history_file.exists():
            try:
                with open(self.config.history_file, encoding="utf-8") as f:
                    self.deployment_history = json.load(f)
            except Exception as exc:
                logger.error(f"Error loading history: {exc}")

    def _save_history(self):
        try:
            self.config.history_file.write_text(
                json.dumps(self.deployment_history, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.error(f"Error saving history: {exc}")

    def _print_help(self):
        self.ui.print_help()
