"""AI engine integration for the custom-topology MicroCloud assistant."""

import json
import logging
import re
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
        self._api_style: Optional[str] = None
        self._resolved_model: Optional[str] = None

    def is_available(self) -> bool:
        """Check if inference engine is running."""
        health_paths = ("/health", "/models", "/v1/models", "/api/tags")
        for path in health_paths:
            try:
                response = requests.get(f"{self.base_url}{path}", timeout=5)
                if response.status_code == 200:
                    return True
            except requests.RequestException:
                continue
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
            # Store full raw JSON so AI remembers its own tool calls on next turn
            self.conversation_history.append(
                {"role": "assistant", "content": response.get("_raw", response.get("content", ""))}
            )
            return response
        except Exception as exc:
            logger.error(f"Error calling inference engine: {exc}")
            return {"content": f"Error: {str(exc)}", "error": True}

    def _call_inference(self, messages: list) -> dict[str, Any]:
        api_style = self._detect_api_style()
        resolved_model = self._resolve_model_name()

        if api_style == "openai":
            payload = {
                "model": resolved_model,
                "messages": messages,
                "stream": False,
            }
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=self.config.response_timeout,
            )
            if response.status_code == 404:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    timeout=self.config.response_timeout,
                )
            response.raise_for_status()
            result = response.json()
            message_payload = result.get("choices", [{}])[0].get("message", {})
            tool_calls = message_payload.get("tool_calls", [])

            if isinstance(tool_calls, list) and tool_calls:
                first_call = tool_calls[0]
                fn = first_call.get("function", {}) if isinstance(first_call, dict) else {}
                tool_name = fn.get("name")
                tool_args_raw = fn.get("arguments", "{}")
                tool_params: dict[str, Any] = {}
                if isinstance(tool_args_raw, str):
                    try:
                        parsed_args = json.loads(tool_args_raw)
                        if isinstance(parsed_args, dict):
                            tool_params = parsed_args
                    except (TypeError, ValueError):
                        tool_params = {}
                elif isinstance(tool_args_raw, dict):
                    tool_params = tool_args_raw

                content = message_payload.get("content", "")
                reasoning_content = message_payload.get("reasoning_content", "")
                return {
                    "action": tool_name,
                    "parameters": tool_params,
                    "message": content or reasoning_content or f"Calling {tool_name}",
                    "_raw": json.dumps(message_payload),
                }

            content = message_payload.get("content", "")
            reasoning_content = message_payload.get("reasoning_content", "")

            # Some local OpenAI-compatible backends may emit reasoning_content while
            # leaving content empty. Prefer content, but fall back to reasoning text.
            raw_content = content if content else reasoning_content
        else:
            payload = {
                "model": resolved_model,
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
            raw_content = result.get("message", {}).get("content", "")

        return _extract_structured_response(raw_content)

    def _detect_api_style(self) -> str:
        """Detect server API shape and cache the result."""
        if self._api_style:
            return self._api_style

        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code == 200:
                self._api_style = "ollama"
                return self._api_style
        except requests.RequestException:
            pass

        for path in ("/v1/models", "/models", "/health"):
            try:
                response = requests.get(f"{self.base_url}{path}", timeout=5)
                if response.status_code == 200:
                    self._api_style = "openai"
                    return self._api_style
            except requests.RequestException:
                continue

        # Fall back to openai-style routes used by current gemma4 snap.
        self._api_style = "openai"
        return self._api_style

    def _resolve_model_name(self) -> str:
        """Pick a concrete model name, allowing broad config defaults like 'gemma4'."""
        if self._resolved_model:
            return self._resolved_model

        candidates = self._fetch_available_models()
        configured = self.model

        if not candidates:
            self._resolved_model = configured
            return self._resolved_model

        for candidate in candidates:
            if candidate == configured:
                self._resolved_model = candidate
                return self._resolved_model

        for candidate in candidates:
            if configured in candidate or candidate.startswith(configured):
                self._resolved_model = candidate
                return self._resolved_model

        self._resolved_model = candidates[0]
        return self._resolved_model

    def _fetch_available_models(self) -> list[str]:
        """Query model list endpoints across supported API styles."""
        endpoints = ("/v1/models", "/models", "/api/tags")

        for endpoint in endpoints:
            try:
                response = requests.get(f"{self.base_url}{endpoint}", timeout=5)
                if response.status_code != 200:
                    continue

                payload = response.json()
                models: list[str] = []

                # OpenAI-style list payload
                for item in payload.get("data", []) if isinstance(payload, dict) else []:
                    model_id = item.get("id")
                    if model_id:
                        models.append(model_id)

                # Gemma/Ollama-style list payload
                for item in payload.get("models", []) if isinstance(payload, dict) else []:
                    model_name = item.get("name") or item.get("model")
                    if model_name:
                        models.append(model_name)

                if models:
                    return list(dict.fromkeys(models))
            except requests.RequestException:
                continue
            except (TypeError, ValueError):
                continue

        return []

    def _get_default_system_prompt(self, include_tools: bool = True) -> str:
        """System prompt focused on custom topology planning."""
        scenario_catalog = scenarios_summary()
        sizing_tiers = _sizing_advisor.describe_tiers()

        prompt = f"""You are a MicroCloud Lab & Demo Environment Assistant.
Your purpose is to help users quickly provision and manage MicroCloud lab, demo,
and teaching environments. This is NOT a production deployment tool — it creates
lightweight environments for learning, testing, proof-of-concepts, and demonstrations.

WHAT YOU DO:
- Deploy MicroCloud clusters inside LXD VMs for lab/demo/teaching purposes.
- Help users understand sizing, topology, and trade-offs for their lab needs.
- Manage environment lifecycle: list, scale, delete lab environments.
- Answer questions about MicroCloud, LXD, and MicroCeph.

WHEN INTRODUCING YOURSELF:
- You are a lab automation assistant, not a cloud architect.
- Emphasize that this tool provisions lab/demo/PoC environments quickly.
- Do NOT claim to be a "senior architect" or imply production-grade design.
- Keep introductions short: explain your capabilities and available commands.

CORE FACTS:
- MicroCloud storage is MicroCeph (no LVM mode).
- OVN is the default network; skip only if user explicitly opts out.
- Node count should be odd (3/5/7) for quorum safety.
- Each MicroCloud node needs a dedicated Ceph disk.
- In this tool's default flow, MicroCloud runs inside LXD VMs created by
  OpenTofu (nested-lxd-lab mode).
- Ceph disks are per-node virtual block volumes provisioned automatically
  by Terraform.

SCRIPT INTEGRATION — CRITICAL:
The deploy_microcloud script ONLY accepts these CLI flags:
  --scenario, --nodes, --sizing-tier, --node-cpu, --node-memory-mb,
  --root-disk-gib, --ceph-disk-gib, --user-prefix, --ssh-key,
  --network-interface, --ovn-uplink-interface, --ceph-osd-disk, --auto-approve
The delete_environment (cleanup) script ONLY accepts: --workspace, --auto-approve
The scale_environment script ONLY accepts:
  --workspace, --target-nodes, --sizing-tier, --node-cpu, --node-memory-mb,
  --root-disk-gib, --ceph-disk-gib, --auto-approve

DO NOT invent or pass parameters that are not in the lists above.
There is NO --deployment-notes, --skip-snaps, --infra-only, or similar flag.
If a user asks for something the scripts don't support (e.g., "deploy without installing snaps"),
explain honestly that the current automation always does a full deployment including snap
installation and MicroCloud initialization.

DEPLOYMENT WORKFLOW (what deploy_microcloud.sh actually does):
1. Detects LXD network and storage pool on the host.
2. Auto-sizes node resources based on host capacity (if not explicitly provided).
3. Runs OpenTofu to create LXD VMs (the infrastructure layer).
4. Runs the Ansible playbook (playbooks/microcloud.yml) which installs snaps and
   initializes MicroCloud inside the VMs.
Steps 3 and 4 ALWAYS run together — there is no option to skip the Ansible step.

SIZING — ALWAYS SHOW DETAILS:
When proposing or confirming a sizing tier, ALWAYS display the per-node resource numbers:
{sizing_tiers}

Example of correct behavior when recommending "small" tier:
  "I recommend the 'small' tier for your PoC:
   - Per node: 4 vCPU / 8 GB RAM / 40 GB root / 50 GB Ceph disk
   - Total (3 nodes): 12 vCPU / 24 GB RAM / 150 GB Ceph storage
   Shall I proceed?"
NEVER say just "small tier" without showing the resource numbers.

PLANNING FLOW:
0. If user asks for lifecycle actions (list/delete/scale), execute those tools directly.
1. Understand user intent (PoC, lab, demo, teaching, etc.).
2. Call inspect_host_environment to check available resources.
3. Propose topology WITH full sizing details shown to the user.
4. Only call deploy_microcloud after explicit user confirmation.

PLANNING MODE SUMMARY:
{scenario_catalog}

OUTPUT CONTRACT:
Always return valid JSON with keys:
- action: tool name or null
- parameters: object (ONLY use parameters that exist in the tool definition)
- message: user-visible explanation
- reasoning: concise reasoning
- needs_confirmation: boolean
- confirmation_prompt: text when confirmation needed
- missing_params: list

RESPONSE STYLE:
- Keep message concise and terminal-friendly (max ~8 lines unless user asks detail).
- Avoid repeating the same paragraph.
- Do not include markdown code fences or pseudo tool blocks in message.
- If calling a tool, keep message to one short plan sentence.
- Always include a non-empty "reasoning" field.
- Use bullet points for comparisons and lists, not walls of text.

DEPLOYMENT SAFETY:
Never call deploy_microcloud until user says yes.
Never invent network interface names or disk paths.
Never pass parameters that are not defined in the tool definitions.
In nested-lxd-lab mode, do not block waiting for host physical OSD disk paths or manual NIC names.

CLEANUP:
Users can request environment deletion at any time.
When asked to delete/clean up, call delete_environment with the workspace name.
Confirm workspace name with user before deletion if not explicitly stated.

ENVIRONMENT MANAGEMENT:
- If user asks to list deployed labs/environments, call list_environments.
- If user asks to add nodes or scale, call scale_environment.
- For scale/delete requests, gather or confirm the target workspace first.
- Never claim success unless the tool execution confirms it.
"""
        if include_tools:
            tools = get_tool_definitions()
            prompt += f"\n\nAvailable tools:\n{json.dumps(tools, indent=2)}"

        return prompt

    def reset_conversation(self):
        self.conversation_history = []

    def get_conversation_history(self) -> list:
        return self.conversation_history.copy()

    def feed_tool_result(self, tool_name: str, result: str) -> dict[str, Any]:
        """Inject a tool result into conversation history and get the AI's next step.

        This closes the agentic loop: the AI sees the tool output and reasons
        further before giving the user a final synthesised response.
        """
        lifecycle_tools = {"list_environments", "delete_environment", "scale_environment"}
        if tool_name in lifecycle_tools:
            followup = (
                f"Tool '{tool_name}' completed. Result:\n\n{result}\n\n"
                "Return a concise user-facing summary of this operation only. "
                "Do not pivot to topology planning unless the user explicitly asks for design/deployment."
            )
        else:
            followup = (
                f"Tool '{tool_name}' completed. Result:\n\n{result}\n\n"
                "Analyze this result and continue your plan. "
                "If you have enough information, give your final recommendation. "
                "If you need another tool, call it."
            )

        self.conversation_history.append({"role": "user", "content": followup})
        system_prompt = self._get_default_system_prompt()
        messages = [{"role": "system", "content": system_prompt}] + self.conversation_history
        try:
            response = self._call_inference(messages)
            self.conversation_history.append(
                {"role": "assistant", "content": response.get("_raw", response.get("content", ""))}
            )
            return response
        except Exception as exc:
            logger.error(f"Error in tool result synthesis: {exc}")
            return {"content": f"Error: {str(exc)}", "error": True}


def _strip_markdown_json(content: str) -> str:
    """Extract likely JSON payload from mixed model text."""
    content = content.strip()

    # If a fenced JSON block exists anywhere, prefer its inner content.
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    # Otherwise attempt to isolate the first complete top-level JSON object.
    obj = _extract_first_json_object(content)
    if obj:
        return obj

    return content


def _extract_first_json_object(content: str) -> str | None:
    """Return the first balanced top-level JSON object found in text."""
    start = content.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False

        for idx in range(start, len(content)):
            ch = content[idx]

            if escaped:
                escaped = False
                continue

            if ch == "\\":
                escaped = True
                continue

            if ch == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return content[start:idx + 1].strip()

        start = content.find("{", start + 1)

    return None


def _extract_structured_response(raw_content: str) -> dict[str, Any]:
    """Parse flexible model outputs into orchestrator-friendly response objects.

    The model is asked to output strict JSON, but some backends occasionally
    return prose mixed with tool snippets (for example <tool_code> blocks).
    This function extracts tool calls when possible and otherwise falls back to
    plain content.
    """
    cleaned = _strip_markdown_json(raw_content)

    # Preferred path: strict JSON object response.
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            parsed["_raw"] = raw_content
            _normalize_tool_fields(parsed)
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback path: <tool_code> ... </tool_code> blocks used by some models.
    tool_block_match = re.search(r"<tool_code>\s*(\{.*?\})\s*</tool_code>", raw_content, flags=re.DOTALL)
    if tool_block_match:
        block = tool_block_match.group(1)
        try:
            tool_payload = json.loads(block)
            action = tool_payload.get("tool_name") or tool_payload.get("tool") or tool_payload.get("action")
            parameters = tool_payload.get("parameters", {})
            prose = re.sub(r"<tool_code>\s*\{.*?\}\s*</tool_code>", "", raw_content, flags=re.DOTALL).strip()
            result = {
                "action": action,
                "parameters": parameters if isinstance(parameters, dict) else {},
                "message": prose,
                "_raw": raw_content,
            }
            _normalize_tool_fields(result)
            return result
        except (TypeError, ValueError):
            pass

    # Fallback path: prose that includes a JSON object with tool fields.
    json_object_match = re.search(r"(\{\s*\"(?:tool_name|tool|action)\".*\})", raw_content, flags=re.DOTALL)
    if json_object_match:
        snippet = json_object_match.group(1)
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                result = {
                    "action": parsed.get("tool_name") or parsed.get("tool") or parsed.get("action"),
                    "parameters": parsed.get("parameters", {}),
                    "message": raw_content,
                    "_raw": raw_content,
                }
                _normalize_tool_fields(result)
                return result
        except (TypeError, ValueError):
            pass

    # Fallback path: plain text tool intent, e.g. "Calling inspect_host_environment".
    # Only match if the extracted tool name is one of the known tools.
    calling_match = re.search(r"\b(?:calling|run(?:ning)?|execute|use)[:\s]+([a-z_][a-z0-9_]*)\b", raw_content, flags=re.IGNORECASE)
    if calling_match:
        extracted_tool = calling_match.group(1)
        # Validate against known tools to avoid false positives.
        known_tools = {t["name"] for t in get_tool_definitions().get("tools", []) if t.get("name")}
        if extracted_tool in known_tools:
            return {
                "action": extracted_tool,
                "parameters": {},
                "message": raw_content,
                "_raw": raw_content,
            }

    return {"content": raw_content, "_raw": raw_content}


def _normalize_tool_fields(payload: dict[str, Any]) -> None:
    """Normalize heterogeneous tool call keys to the expected orchestrator schema."""
    if not payload.get("action"):
        payload["action"] = payload.get("tool_name") or payload.get("tool")

    if "parameters" not in payload or not isinstance(payload.get("parameters"), dict):
        payload["parameters"] = {}
