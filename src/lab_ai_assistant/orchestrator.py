"""Lab orchestration and execution layer for MicroCloud-only workflows."""

import json
import logging
import subprocess
from datetime import datetime
from typing import Any
from lab_ai_assistant.config import Config
from lab_ai_assistant.ai_engine import AIEngine
from lab_ai_assistant.tools import validate_tool_parameters

logger = logging.getLogger(__name__)


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
