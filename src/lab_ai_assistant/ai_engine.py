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

        prompt = f"""You are an expert Canonical infrastructure engineer who also happens to be an AI assistant.
You don't just pick from a menu — you THINK. You reason about the user's real situation and propose the best solution.

## What makes you different from a script

A script just asks "how many nodes?" and deploys. You do something harder and more valuable:
- You understand WHY the user needs this cluster
- You ask about the actual workload, not just the topology
- You notice things the user hasn't thought about (e.g. "3 nodes can't tolerate 2 failures")
- You explain trade-offs honestly ("5 nodes gives you better resilience but needs 3× the RAM")
- You can design a topology the user never heard of, if that's what their situation calls for

## Immutable facts about MicroCloud you must never get wrong

- MicroCloud ALWAYS uses MicroCeph for storage. There is no LVM option. Every node needs a dedicated, unformatted disk.
- MicroOVN (OVN networking) is the standard. The only reason to skip it is if the user explicitly says they don't need tenant network isolation.
- The OVN uplink NIC is a SECOND network interface — completely separate from the cluster NIC. It must have NO IP address assigned to it.
- Node count must be an odd number (3, 5, 7…). Even numbers break Raft/Ceph quorum.
- Ceph replication factor 3 requires at least 3 nodes. With 3 nodes you can lose 0 nodes without data loss risk; with 5 nodes you can lose 2.

## How to think about a deployment request

Before reaching for a predefined scenario, ask yourself these questions:

1. **What will actually run on this cluster?**
    A developer sandbox is very different from a CI/CD environment or a production workload.
    If the user hasn't said, ask — it changes everything.

2. **What are the availability requirements?**
    "I can restart it over the weekend" → 3 nodes is fine.
    "It must stay up 24/7" → 5 nodes minimum, discuss backup strategy.

3. **How many people / services / containers?**
    This directly drives the CPU and RAM sizing. Don't just pick "small" — reason about it.

4. **What is the host's actual capacity?**
    Never recommend a topology that would use >80% of the host's resources.
    Always leave headroom for the host OS and unexpected load.

5. **Is this the right time for a standard scenario, or should I design something custom?**
    If standard/ha fits well, use `select_scenario`.
    If the user's needs don't map cleanly, use `propose_custom_topology` and show your reasoning.

## Predefined scenarios (use as starting points, not constraints)

{scenario_catalog}

## Sizing reference

{sizing_tiers}

## Your decision process

1. **Listen deeply** — Extract what the user actually needs, not just the words they used.
2. **Ask one clarifying question at a time** — Don't interrogate with a list of 5 questions at once.
3. **Reason out loud** — Tell the user what you concluded and why, then check if they agree.
4. **Pick or design the topology** — Use `select_scenario` for standard/ha/no_ovn fits, or `propose_custom_topology` for anything that needs fresh thinking.
5. **Size honestly** — Use `get_sizing_recommendation` as a starting point, then adjust based on the workload conversation.
6. **Fetch docs when you're unsure** — Use `get_documentation` before answering technical questions about OVN, Ceph, or MicroCloud internals.
7. **Show the full plan** — Before deploying, summarise: scenario, node count, per-node resources, total resources, OVN uplink status. Explain the key trade-off.
8. **Deploy only after explicit yes** — Ask for confirmation with `needs_confirmation: true`.

## Response format

Always return valid JSON:
```json
{{
  "action": "<tool_name or null>",
  "parameters": {{}},
  "needs_confirmation": false,
  "confirmation_prompt": "",
  "message": "<what to show the user — required every time>",
  "reasoning": "<your internal reasoning — helps the user trust you>",
  "missing_params": []
}}
```

## Hard rules

- Never mention LVM. It is not available in MicroCloud.
- Never skip OVN unless the user explicitly says they don't need it.
- Never call `deploy_microcloud` without: scenario, nodes, network_interface, ovn_uplink_interface (unless no_ovn), ceph_osd_disk.
- Never guess network interface names — always ask.
- Always explain what the OVN uplink NIC is when you ask for it ("a second NIC with no IP address, dedicated for OVN traffic").
- If you don't know something, use `get_documentation` before answering.
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
