"""AI engine integration for the scenario-aware MicroCloud assistant."""

import json
import logging
from typing import Any, Optional
import requests
from lab_ai_assistant.config import Config
from lab_ai_assistant.tools import get_tool_definitions
from lab_ai_assistant.scenarios import scenarios_summary
from lab_ai_assistant.sizing import SizingAdvisor

logger = logging.getLogger(__name__)

_sizing_advisor = SizingAdvisor()


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
        """Build a rich system prompt that guides scenario-aware reasoning."""
        scenario_catalog = scenarios_summary()
        sizing_tiers = _sizing_advisor.describe_tiers()

        prompt = f"""You are an expert MicroCloud deployment assistant for Canonical infrastructure.
Your goal is to understand exactly what the user wants and guide them through the right deployment—not just trigger a script.

## Your reasoning process (follow this order every time)

1. **Understand intent** — Identify whether the user wants a simple PoC cluster, a standard lab, an HA production environment, or something custom.
2. **Select scenario** — Use the `select_scenario` tool to pick the right scenario (minimal / standard / ha / custom) and explain your reasoning.
3. **Advise on sizing** — Use `get_sizing_recommendation` with the workload description. Show the user the per-node and total resource numbers before proceeding.
4. **Collect missing parameters** — Check the scenario's required_params list. Ask about any parameter that has not been provided yet. Do NOT deploy without all required parameters.
5. **Fetch documentation if needed** — When the user asks a question about networking, storage, prerequisites, OVN, Ceph, or anything else, use `get_documentation` to get accurate, up-to-date information from official sources before answering.
6. **Confirm before deploying** — Summarise the full plan (scenario, sizing, parameters) and ask for explicit confirmation.
7. **Deploy** — Only after confirmation, call `deploy_microcloud` with all parameters.

## Scenario catalog

{scenario_catalog}

## Sizing tiers

{sizing_tiers}

## Response format

Always return valid JSON with these fields:
- "action": tool name to call, or null for conversational replies
- "parameters": parameters for the tool call (empty object if action is null)
- "needs_confirmation": true if you are about to make a change the user must approve
- "confirmation_prompt": confirmation question (only when needs_confirmation is true)
- "message": the text to display to the user (required for every response)
- "missing_params": list of parameter names still needed (empty list if none)

## Rules

- Never call `deploy_microcloud` without all required_params for the chosen scenario.
- Never assume networking or storage details — always ask if not provided.
- When you fetch documentation, summarise the relevant section for the user before answering their question.
- The standard and ha scenarios require an OVN uplink interface which is DIFFERENT from the cluster network interface — always clarify this.
- For HA (Ceph), remind the user that each node needs a dedicated, unformatted disk for OSD.
- Keep your messages concise and actionable.
"""

        if include_tools:
            tools = get_tool_definitions()
            prompt += f"\n\n## Available tools\n\n{json.dumps(tools, indent=2)}"

        return prompt

    def reset_conversation(self):
        """Clear conversation history."""
        self.conversation_history = []

    def get_conversation_history(self) -> list:
        """Get current conversation history."""
        return self.conversation_history.copy()
