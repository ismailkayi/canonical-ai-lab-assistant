"""Lab orchestration and execution layer for scenario-aware MicroCloud workflows."""

import json
import logging
import subprocess
from datetime import datetime
from typing import Any
from lab_ai_assistant.config import Config
from lab_ai_assistant.ai_engine import AIEngine
from lab_ai_assistant.tools import validate_tool_parameters
from lab_ai_assistant.scenarios import get_scenario, scenarios_summary
from lab_ai_assistant.sizing import SizingAdvisor, SizingTier
from lab_ai_assistant.doc_fetcher import DocFetcher

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

    # ------------------------------------------------------------------
    # Internal processing
    # ------------------------------------------------------------------

    def _process_user_input(self, user_message: str) -> str:
        ai_response = self.ai_engine.chat(user_message)

        if ai_response.get("error"):
            return ai_response.get("content", "An error occurred")

        # Agent may return a "message" field (new contract) or plain "content"
        display_message = ai_response.get("message") or ai_response.get("content", "")

        action = ai_response.get("action")
        if not action:
            return display_message

        parameters = ai_response.get("parameters", {})

        # Locally handled tools (no subprocess needed)
        local_result = self._handle_local_tool(action, parameters)
        if local_result is not None:
            return local_result

        # Parameter validation for subprocess tools
        is_valid, error_msg = validate_tool_parameters(action, parameters)
        if not is_valid:
            return f"I still need more information: {error_msg}"

        # Confirmation gate
        if ai_response.get("needs_confirmation"):
            confirmation_prompt = ai_response.get(
                "confirmation_prompt", "Shall I proceed?"
            )
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
        """
        Execute tools that don't require an external script.
        Returns a formatted string if handled, None if the caller should proceed.
        """
        if action == "select_scenario":
            scenario_name = parameters.get("scenario", "standard")
            reason = parameters.get("reason", "")
            scenario = get_scenario(scenario_name)
            if not scenario:
                return f"Unknown scenario: {scenario_name}"
            lines = [
                f"Selected scenario: **{scenario.label}**",
                f"  {scenario.description}",
                f"  Nodes: {scenario.default_nodes} (min {scenario.min_nodes})",
                f"  Network: {scenario.network_mode.value}",
                f"  Storage: {scenario.storage_backend.value}",
                f"  Required parameters: {', '.join(scenario.required_params)}",
            ]
            if reason:
                lines.insert(1, f"  Reason: {reason}")
            if scenario.notes:
                lines.append(f"  ⚠  {scenario.notes}")
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
            return f"📖 **{title}**\nSource: {doc['url']}\n\n{content[:2500]}"

        return None  # not a local tool

    def _execute_action(self, action: str, parameters: dict[str, Any]) -> str:
        """Execute a script-backed action."""
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
            capture_output=True, text=True,
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
                with open(self.config.history_file) as f:
                    self.deployment_history = json.load(f)
            except Exception as exc:
                logger.error(f"Error loading history: {exc}")

    def _save_history(self):
        try:
            self.config.history_file.write_text(
                json.dumps(self.deployment_history, indent=2)
            )
        except Exception as exc:
            logger.error(f"Error saving history: {exc}")

    def _print_help(self):
        print("""
Commands:
  "scenarios"        - List all available deployment scenarios
  "sizing"           - Show sizing tiers (minimal / small / medium / large)

Chat examples:
  "I need a small PoC cluster for testing"
  "Deploy 5-node HA MicroCloud for staging"
  "What are the storage requirements for Ceph?"
  "Prepare this host for MicroCloud"

Then follow the assistant's questions to fill in networking and storage details.
""")



class LabOrchestrator:
    """Main orchestration layer for lab automation."""

    def __init__(self, config: Config):
        """Initialize orchestrator."""
        self.config = config
        self.ai_engine = AIEngine(config)
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

        logger.info("Starting chat session")
        print("\n" + "="*60)
        print("Canonical AI Lab Assistant - MicroCloud first")
        print("="*60)
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

                response = self._process_user_input(user_input)
                print(f"\nAssistant: {response}\n")

            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except Exception as e:
                logger.error(f"Error in chat loop: {e}")
                print(f"Error: {e}\n")

    def _process_user_input(self, user_message: str) -> str:
        """
        Process user input through AI engine.

        Args:
            user_message: User's message

        Returns:
            Response to display to user
        """
        ai_response = self.ai_engine.chat(user_message)

        if ai_response.get("error"):
            return ai_response.get("content", "An error occurred")

        action = ai_response.get("action")
        if not action:
            return ai_response.get("content", "No response")

        parameters = ai_response.get("parameters", {})

        is_valid, error_msg = validate_tool_parameters(action, parameters)
        if not is_valid:
            return f"Parameter validation failed: {error_msg}\nPlease provide: {error_msg}"

        if ai_response.get("needs_confirmation"):
            confirmation_prompt = ai_response.get("confirmation_prompt", "Proceed?")
            return f"{ai_response.get('explanation', '')}\n\n{confirmation_prompt}\n[User must confirm]"

        logger.info(f"Executing action: {action} with parameters: {parameters}")
        result = self._execute_action(action, parameters)

        self._record_deployment(action, parameters, result)

        return f"Action: {action}\nParameters: {json.dumps(parameters, indent=2)}\n\nResult:\n{result}"

    def _execute_action(self, action: str, parameters: dict[str, Any]) -> str:
        """Execute a MicroCloud action using repo scripts.

        Args:
            action: Action name (prep_host, install_inference_snap, deploy_microcloud)
            parameters: Action parameters

        Returns:
            Execution result
        """
        try:
            scenario_map = {
                "prep_host": self.config.prep_host_script,
                "install_inference_snap": self.config.install_inference_script,
                "deploy_microcloud": self.config.deploy_microcloud_script,
            }

            script_path = scenario_map.get(action)
            if not script_path:
                return f"Unknown action: {action}"

            cmd = ["bash", str(script_path)]

            for key, value in parameters.items():
                if value is not None:
                    cmd.append(f"--{key}={value}")

            if action == "deploy_microcloud":
                cmd.append("--auto-approve")

            logger.info(f"Executing command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.response_timeout,
                cwd=self.config.repo_root,
            )

            if result.returncode != 0:
                return f"Execution failed:\n{result.stderr}"

            return result.stdout

        except subprocess.TimeoutExpired:
            return "Action timed out"
        except Exception as e:
            logger.error(f"Action execution error: {e}")
            return f"Error: {str(e)}"

    def _run_script(self, script_path) -> str:
        """Run a repo script and return its output."""
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

    def _record_deployment(
        self,
        action: str,
        parameters: dict[str, Any],
        result: str
    ):
        """Record deployment in history."""
        record = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "parameters": parameters,
            "success": "failed" not in result.lower(),
            "result_preview": result[:200]
        }
        self.deployment_history.append(record)
        self._save_history()

    def _load_history(self):
        """Load deployment history from file."""
        if self.config.history_file.exists():
            try:
                with open(self.config.history_file) as f:
                    self.deployment_history = json.load(f)
            except Exception as e:
                logger.error(f"Error loading history: {e}")

    def _save_history(self):
        """Save deployment history to file."""
        try:
            self.config.history_file.write_text(
                json.dumps(self.deployment_history, indent=2)
            )
        except Exception as e:
            logger.error(f"Error saving history: {e}")

    def _print_help(self):
        """Print help information."""
        help_text = """
Commands:
    - "Prepare this host" - Install prerequisites and the inference snap
    - "Deploy 3 node microcloud setup" - Deploy MicroCloud
    - "Use eth0 and LVM storage" - Fill missing deployment parameters
  - "help" - Show this message
  - "quit" - Exit

Examples:
    You: Prepare this host for MicroCloud
    Assistant: [Runs prep_host and installs inference snap]
    You: Deploy 3 node microcloud with eth0 and LVM storage
    Assistant: [Asks for missing details, then deploys]
"""
        print(help_text)
