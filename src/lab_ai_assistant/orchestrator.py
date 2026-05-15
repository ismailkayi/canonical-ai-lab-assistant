"""Lab orchestration and execution layer."""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Optional
from datetime import datetime
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

    def start_chat(self):
        """Start interactive chat session."""
        if not self.ai_engine.is_available():
            raise RuntimeError(
                f"Inference engine not available at {self.config.inference_host}\n"
                f"Please start it with: snap install {self.config.inference_engine}"
            )

        logger.info("Starting chat session")
        print("\n" + "="*60)
        print("Canonical Lab AI Assistant")
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

                # Process user message through AI
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
        # Send to AI engine
        ai_response = self.ai_engine.chat(user_message)

        if ai_response.get("error"):
            return ai_response.get("content", "An error occurred")

        # Check if AI detected an action
        action = ai_response.get("action")
        if not action:
            # Just a conversational response
            return ai_response.get("content", "No response")

        # Action detected - needs parameter validation and execution
        parameters = ai_response.get("parameters", {})

        # Validate parameters
        is_valid, error_msg = validate_tool_parameters(action, parameters)
        if not is_valid:
            return f"Parameter validation failed: {error_msg}\nPlease provide: {error_msg}"

        # Check if confirmation needed
        if ai_response.get("needs_confirmation"):
            confirmation_prompt = ai_response.get("confirmation_prompt", "Proceed?")
            return f"{ai_response.get('explanation', '')}\n\n{confirmation_prompt}\n[User must confirm]"

        # Execute action
        logger.info(f"Executing action: {action} with parameters: {parameters}")
        result = self._execute_action(action, parameters)

        # Log to history
        self._record_deployment(action, parameters, result)

        return f"Action: {action}\nParameters: {json.dumps(parameters, indent=2)}\n\nResult:\n{result}"

    def _execute_action(self, action: str, parameters: dict[str, Any]) -> str:
        """
        Execute an action using orchestrate.sh.

        Args:
            action: Action name (deploy_microcloud, etc.)
            parameters: Action parameters

        Returns:
            Execution result
        """
        try:
            # Map action to orchestrate.sh scenario
            scenario_map = {
                "deploy_microcloud": "microcloud",
                "deploy_k8s_snap": "k8s-snap",
                "deploy_k8s_juju": "k8s-juju",
                "manage_lab": "manage",
                "get_lab_status": "status",
            }

            scenario = scenario_map.get(action)
            if not scenario:
                return f"Unknown action: {action}"

            # Build command
            cmd = [
                str(self.config.orchestrate_script),
                "--non-interactive",
                "--scenario", scenario,
            ]

            # Add parameters as flags
            for key, value in parameters.items():
                if value is not None:
                    cmd.append(f"--{key}={value}")

            cmd.append("--auto-approve")

            logger.info(f"Executing command: {' '.join(cmd)}")

            # Execute
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.response_timeout,
                cwd=self.config.lab_scripts_path
            )

            if result.returncode != 0:
                return f"Execution failed:\n{result.stderr}"

            return result.stdout

        except subprocess.TimeoutExpired:
            return "Action timed out"
        except Exception as e:
            logger.error(f"Action execution error: {e}")
            return f"Error: {str(e)}"

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
  - "Deploy 3 node microcloud setup" - Deploy MicroCloud
  - "Deploy K8s with 3 control planes and 2 workers" - Deploy Kubernetes
  - "Show deployment status" - Get current lab status
  - "Delete the current deployment" - Clean up
  - "help" - Show this message
  - "quit" - Exit

Examples:
  You: Deploy me 3 node microcloud with eth0 network
  Assistant: [Asks for clarification on storage]
  You: Use LVM with 50GB
  Assistant: [Summarizes and asks for confirmation]
  You: Yes, proceed
  Assistant: [Executes deployment]
"""
        print(help_text)
