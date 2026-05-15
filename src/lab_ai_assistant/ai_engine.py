"""AI engine integration for the scenario-aware MicroCloud assistant."""

import json
import logging
from typing import Any, Optional

import requests

from lab_ai_assistant.config import Config
from lab_ai_assistant.scenarios import scenarios_summary
from lab_ai_assistant.sizing import SizingAdvisor
from lab_ai_assistant.tools import get_tool_definitions

logger = logging.getLogger(__name__)

_sizing_advisor = SizingAdvisor()


class AIEngine:
    """Interface to Canonical inference snap engine."""

    def __init__(self, config: Config):
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
        """Send a user message to the local LLM and parse structured response."""
        self.conversation_history.append({"role": "user", "content": user_message})

        if system_prompt is None:
            system_prompt = self._get_default_system_prompt(include_tools)

        messages = [{"role": "system", "content": system_prompt}] + self.conversation_history

        try:
            response = self._call_inference(messages)
            self.conversation_history.append(
                {"role": "assistant", "content": response.get("content", "")}
            )
            return response
        except Exception as exc:
            logger.error(f"Error calling inference engine: {exc}")
            return {"content": f"Error: {str(exc)}", "error": True}

    def _call_inference(self, messages: list) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
        }

        response = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=self.config.response_timeout,
        )
        response.raise_for_status()

        result = response.json()
        content = result.get("message", {}).get("content", "")

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"content": content}

    def _get_default_system_prompt(self, include_tools: bool = True) -> str:
        """System prompt focused on planning quality, not scenario matching."""
        scenario_catalog = scenarios_summary()
        sizing_tiers = _sizing_advisor.describe_tiers()

        prompt = f"""You are a senior Canonical platform architect.
Your job is to DESIGN the right MicroCloud topology, not just map text to a fixed scenario.

CORE IDEA:
- A script executes.
- You analyze, compare options, and justify decisions.

NON-NEGOTIABLE FACTS:
- MicroCloud storage is MicroCeph (no LVM mode).
- OVN is the default; skip OVN only if user explicitly opts out.
- Node count should be odd (3/5/7) for quorum safety.
- Each node needs a dedicated, unformatted Ceph disk.

REQUIRED PLANNING FLOW:
1. Understand workload and business intent (PoC, team size, uptime expectations, budget).
2. Call inspect_host_environment before final sizing decisions.
3. If uncertain on technical details, call get_documentation.
4. Either:
   - choose a baseline scenario via select_scenario, OR
   - design from scratch via propose_custom_topology.
5. Explain trade-offs and provide one alternative design.
6. Ask for missing parameters one by one.
7. Only call deploy_microcloud after explicit user confirmation.

WHAT "MORE AI" LOOKS LIKE:
- Explain WHY a 5-node cluster is better/worse than 3-node for this workload.
- Detect mismatch between user ask and host capacity; suggest compromises.
- Suggest phased rollouts (start 3, scale to 5) when practical.
- Ground claims with docs when the user asks "why".

SCENARIO CATALOG (baseline only, not a hard cage):
{scenario_catalog}

SIZING REFERENCE:
{sizing_tiers}

OUTPUT CONTRACT:
Always return valid JSON with keys:
- action: tool name or null
- parameters: object
- message: user-visible explanation
- reasoning: concise architectural reasoning
- needs_confirmation: boolean
- confirmation_prompt: text when confirmation needed
- missing_params: list

DEPLOYMENT SAFETY:
Never call deploy_microcloud until user says yes.
Never invent network interface names or disk paths.
"""

        if include_tools:
            tools = get_tool_definitions()
            prompt += f"\n\nAvailable tools:\n{json.dumps(tools, indent=2)}"

        return prompt

    def reset_conversation(self):
        self.conversation_history = []

    def get_conversation_history(self) -> list:
        return self.conversation_history.copy()
