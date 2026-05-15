"""Lab orchestration and execution layer for scenario-aware MicroCloud workflows."""

import json
import logging
import subprocess
from datetime import datetime
from typing import Any

from lab_ai_assistant.ai_engine import AIEngine
from lab_ai_assistant.config import Config
from lab_ai_assistant.doc_fetcher import DocFetcher
from lab_ai_assistant.scenarios import get_scenario, scenarios_summary
from lab_ai_assistant.sizing import SizingAdvisor, SizingTier
from lab_ai_assistant.tools import validate_tool_parameters

logger = logging.getLogger(__name__)


class LabOrchestrator:
    """Main orchestration layer for MicroCloud lab automation."""

    def __init__(self, config: Config):
        self.config = config
        self.ai_engine = AIEngine(config)
        self.sizing_advisor = SizingAdvisor()
        self.doc_fetcher = DocFetcher(cache_dir=config.state_dir)
        self.deployment_history = []
        self._load_history()

    def bootstrap_host(self) -> str:
        """Prepare the host and install the inference snap."""
        prep_output = self._run_script(self.config.prep_host_script)
        return f"Host preparation completed.\n\nPrep host output:\n{prep_output}"

    def start_chat(self):
        """Start interactive chat session."""
        if not self.ai_engine.is_available():
            raise RuntimeError(
                f"Inference engine not available at {self.config.inference_host}\n"
                f"Please start it with: snap install {self.config.inference_engine}"
            )

        print("\n" + "=" * 60)
        print("Canonical AI Lab Assistant — MicroCloud")
        print("=" * 60)
        print("Type 'help' for commands, 'quit' to exit\n")

        while True:
            try:
                user_input = input("You: ").strip()
                if not user_input:
                    continue
                if user_input.lower() == "quit":
                    print("Goodbye!")
                    break
                if user_input.lower() == "help":
                    self._print_help()
                    continue
                if user_input.lower() in ("scenarios", "list scenarios"):
                    print(scenarios_summary())
                    continue
                if user_input.lower().startswith("sizing"):
                    print(self.sizing_advisor.describe_tiers())
                    continue

                response = self._process_user_input(user_input)
                print(f"\nAssistant: {response}\n")

            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except Exception as exc:
                logger.error(f"Error in chat loop: {exc}")
                print(f"Error: {exc}\n")

    def _process_user_input(self, user_message: str) -> str:
        ai_response = self.ai_engine.chat(user_message)

        if ai_response.get("error"):
            return ai_response.get("content", "An error occurred")

        display_message = ai_response.get("message") or ai_response.get("content", "")
        reasoning = ai_response.get("reasoning", "")
        if reasoning:
            display_message = (
                f"[thinking: {reasoning}]\n\n{display_message}"
                if display_message
                else f"[thinking: {reasoning}]"
            )

        action = ai_response.get("action")
        if not action:
            return display_message

        parameters = ai_response.get("parameters", {})

        local_result = self._handle_local_tool(action, parameters)
        if local_result is not None:
            return f"{display_message}\n\n{local_result}" if display_message else local_result

        is_valid, error_msg = validate_tool_parameters(action, parameters)
        if not is_valid:
            return f"I still need more information: {error_msg}"

        if ai_response.get("needs_confirmation"):
            confirmation_prompt = ai_response.get("confirmation_prompt", "Shall I proceed?")
            return (
                f"{display_message}\n\n"
                f"{confirmation_prompt}\n"
                f"(Reply 'yes' to confirm)"
            )

        logger.info(f"Executing action: {action} params={parameters}")
        result = self._execute_action(action, parameters)
        self._record_deployment(action, parameters, result)

        return f"{display_message}\n\nResult:\n{result}" if display_message else f"Result:\n{result}"

    def _handle_local_tool(self, action: str, parameters: dict[str, Any]) -> str | None:
        """Execute tools that do not require external scripts."""
        if action == "inspect_host_environment":
            return self._inspect_host_environment()

        if action == "select_scenario":
            scenario_name = parameters.get("scenario", "standard")
            reason = parameters.get("reason", "")
            scenario = get_scenario(scenario_name)
            if not scenario:
                return f"Unknown scenario: {scenario_name}"
            lines = [
                f"Selected scenario: {scenario.label}",
                f"  {scenario.description}",
                f"  Nodes: {scenario.default_nodes} (min {scenario.min_nodes})",
                f"  Network: {scenario.network_mode.value}",
                f"  Storage: {scenario.storage_backend.value}",
                f"  Required parameters: {', '.join(scenario.required_params)}",
            ]
            if reason:
                lines.insert(1, f"  Reason: {reason}")
            if scenario.notes:
                lines.append(f"  Note: {scenario.notes}")
            return "\n".join(lines)

        if action == "propose_custom_topology":
            nodes = int(parameters.get("node_count", 3))
            cpu = int(parameters.get("node_cpu", 2))
            ram = int(parameters.get("node_ram_gb", 8))
            root = int(parameters.get("root_disk_gb", 40))
            ceph = int(parameters.get("ceph_disk_gb", 50))
            ovn = bool(parameters.get("use_ovn", True))
            replication = int(parameters.get("ceph_replication_factor", 3))
            reasoning = parameters.get("reasoning", "")
            trade_offs = parameters.get("trade_offs", "")
            alternative = parameters.get("alternative", "")

            total_cpu = nodes * cpu
            total_ram = nodes * ram
            total_ceph_raw = nodes * ceph
            total_ceph_usable = int(total_ceph_raw / max(replication, 1))

            lines = [
                "Custom topology proposal",
                "",
                f"  Nodes            : {nodes}",
                f"  vCPU / node      : {cpu} (total: {total_cpu} vCPU)",
                f"  RAM / node       : {ram} GB (total: {total_ram} GB)",
                f"  Root disk / node : {root} GB",
                f"  Ceph disk / node : {ceph} GB",
                f"  Ceph capacity    : ~{total_ceph_usable} GB usable ({total_ceph_raw} GB raw, replication {replication}x)",
                f"  OVN networking   : {'enabled' if ovn else 'disabled (explicit opt-out)'}",
            ]
            if reasoning:
                lines += ["", "Why:", f"  {reasoning}"]
            if trade_offs:
                lines += ["", "Trade-offs:", f"  {trade_offs}"]
            if alternative:
                lines += ["", "Alternative:", f"  {alternative}"]
            return "\n".join(lines)

        if action == "get_sizing_recommendation":
            scenario_name = parameters.get("scenario", "standard")
            nodes = parameters.get("nodes")
            workload = parameters.get("workload_description", "")
            tier_str = parameters.get("tier")
            tier = SizingTier(tier_str) if tier_str else None
            rec = self.sizing_advisor.recommend(
                scenario_name=scenario_name,
                nodes=nodes,
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
            return f"Doc: {title}\nSource: {doc['url']}\n\n{content[:2500]}"

        return None

    def _inspect_host_environment(self) -> str:
        """Collect host facts for grounded planning decisions."""
        commands = {
            "cpu_cores": "nproc 2>/dev/null || echo unknown",
            "ram_mb": "awk '/MemTotal:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo unknown",
            "disks": "lsblk -dn -o NAME,SIZE,TYPE | awk '$3==\"disk\" {print $1\":\"$2}' | paste -sd ', ' -",
            "lxd_networks": "lxc network list --format csv 2>/dev/null | awk -F',' '{print $1}' | paste -sd ', ' -",
            "lxd_storage_pools": "lxc storage list --format csv 2>/dev/null | awk -F',' '{print $1}' | paste -sd ', ' -",
        }

        results: dict[str, str] = {}
        for key, cmd in commands.items():
            try:
                res = subprocess.run(
                    ["bash", "-lc", cmd],
                    capture_output=True,
                    text=True,
                    cwd=self.config.repo_root,
                    timeout=10,
                )
                out = (res.stdout or "").strip()
                results[key] = out if out else "unknown"
            except Exception:
                results[key] = "unknown"

        return (
            "Host environment snapshot\n"
            f"  CPU cores        : {results.get('cpu_cores', 'unknown')}\n"
            f"  RAM              : {results.get('ram_mb', 'unknown')} MB\n"
            f"  Disk devices     : {results.get('disks', 'unknown')}\n"
            f"  LXD networks     : {results.get('lxd_networks', 'unknown')}\n"
            f"  LXD storage pool : {results.get('lxd_storage_pools', 'unknown')}"
        )

    def _execute_action(self, action: str, parameters: dict[str, Any]) -> str:
        """Execute script-backed actions."""
        try:
            script_map = {
                "prep_host": self.config.prep_host_script,
                "install_inference_snap": self.config.install_inference_script,
                "deploy_microcloud": self.config.deploy_microcloud_script,
            }

            script_path = script_map.get(action)
            if not script_path:
                return f"No script mapped for action: {action}"

            cmd = ["bash", str(script_path)]
            for key, value in parameters.items():
                if value is not None:
                    cmd.append(f"--{key.replace('_', '-')}={value}")

            if action == "deploy_microcloud":
                cmd.append("--auto-approve")

            logger.info(f"Running: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.response_timeout,
                cwd=self.config.repo_root,
            )

            if result.returncode != 0:
                return f"Script failed:\n{result.stderr or result.stdout}"

            return result.stdout or "(no output)"

        except subprocess.TimeoutExpired:
            return "Action timed out."
        except Exception as exc:
            logger.error(f"Action execution error: {exc}")
            return f"Error: {exc}"

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
            "success": "failed" not in result.lower(),
            "result_preview": result[:200],
        }
        self.deployment_history.append(record)
        self._save_history()

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
        print(
            """
Commands:
  scenarios   - list baseline scenarios
  sizing      - show sizing tiers
  help        - show this help
  quit        - exit

Tips:
  Ask naturally: "I need a staging cluster for 20 developers"
  The assistant will inspect host resources, propose topology, explain trade-offs,
  and ask confirmation before deployment.
"""
        )
