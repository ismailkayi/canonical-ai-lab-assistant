"""AI engine integration for the MicroCloud-first workflow."""

import json
import logging
from typing import Any, Optional
import requests
from lab_ai_assistant.config import Config
from lab_ai_assistant.tools import get_tool_definitions

logger = logging.getLogger(__name__)


class AIEngine:
    """Interface to Canonical inference snap engine."""

    def __init__(self, config: Config):
        """Initialize AI engine."""
        self.config = config
        self.base_url = config.inference_host
        self.model = config.inference_model
        self.conversation_history = []

    def is_available(self) -> bool:
        """Check if inference engine is running."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except requests.RequestException:
            logger.error(f"Inference engine not available at {self.base_url}")
            return False

    def chat(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        include_tools: bool = True,
    ) -> dict[str, Any]:
        """
        Send message to AI engine and get response.

        Args:
            user_message: User's input message
            system_prompt: Optional custom system prompt
            include_tools: Whether to include tool definitions

        Returns:
            AI response as dictionary with:
            - content: Main response text
            - action: Detected action (if tool calling)
            - parameters: Extracted parameters
            - needs_confirmation: Whether action needs confirmation
        """
        # Add to conversation history
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        # Prepare system prompt
        if system_prompt is None:
            system_prompt = self._get_default_system_prompt(include_tools)

        # Build messages with history
        messages = [
            {"role": "system", "content": system_prompt}
        ] + self.conversation_history

        # Call inference engine
        try:
            response = self._call_inference(messages)
            
            # Add assistant response to history
            self.conversation_history.append({
                "role": "assistant",
                "content": response.get("content", "")
            })

            return response

        except Exception as e:
            logger.error(f"Error calling inference engine: {e}")
            return {
                "content": f"Error: {str(e)}",
                "error": True
            }

    def _call_inference(self, messages: list) -> dict[str, Any]:
        """Call the inference engine API."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.config.response_timeout
            )
            response.raise_for_status()

            result = response.json()
            
            # Extract response content
            content = result.get("message", {}).get("content", "")
            
            # Try to parse as JSON (for tool calling)
            try:
                parsed = json.loads(content)
                return parsed
            except json.JSONDecodeError:
                # Return as plain text
                return {"content": content}

        except requests.RequestException as e:
            logger.error(f"Inference API error: {e}")
            raise

    def _get_default_system_prompt(self, include_tools: bool = True) -> str:
        """Get default system prompt for lab automation."""
        prompt = """You are an AI Lab Assistant for Canonical infrastructure automation.
You help users prepare hosts, install the local inference snap, and deploy MicroCloud.

Your capabilities:
- Prepare the Ubuntu host for MicroCloud work
- Install the Canonical inference snap used by this assistant
- Deploy MicroCloud clusters
- Provide guidance on MicroCloud prerequisites and sizing

When users request deployments:
1. Analyze their requirements
2. Ask for clarification on missing MicroCloud parameters (network interface, storage type, storage size, and node count)
3. Prefer MicroCloud-only answers; do not mention Kubernetes yet
4. Execute with user confirmation for any host changes or deployment actions

Return responses as JSON with:
- "action": The operation to perform (prep_host, install_inference_snap, deploy_microcloud, get_documentation)
- "parameters": Extracted configuration parameters
- "needs_confirmation": true/false - ask before destructive operations
- "confirmation_prompt": Question to ask user (if needs_confirmation=true)
- "explanation": Plain English explanation of what will happen

Documentation reference: https://documentation.ubuntu.com/inference-snaps/ and the MicroCloud docs
"""
        
        if include_tools:
            tools = get_tool_definitions()
            prompt += f"\n\nAvailable tools:\n{json.dumps(tools, indent=2)}"

        return prompt

    def reset_conversation(self):
        """Clear conversation history."""
        self.conversation_history = []

    def get_conversation_history(self) -> list:
        """Get current conversation history."""
        return self.conversation_history.copy()
