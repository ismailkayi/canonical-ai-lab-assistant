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
- Each MicroCloud node needs at least one Ceph disk (default: 1 OSD per node).
- In this tool's default flow, MicroCloud runs inside LXD VMs created by
  OpenTofu (nested-lxd-lab mode).
- Ceph disks are per-node virtual block volumes provisioned automatically
  by Terraform.
- ceph_disks_per_node: 1 is the default; recommend 2 for higher IOPS or larger
  clusters. Each OSD disk uses ceph_disk_gib GiB of host storage.
- local_disk_gib: 0 = disabled (default). Set >= 10 to add a local ZFS disk
  per node for fast local storage alongside distributed Ceph.

SYSTEM ARCHITECTURE — HOW THE AUTOMATION WORKS:
This system has a layered pipeline. Understand each layer so you can accurately
tell users what is and isn't possible with the current automation.

┌─────────────────────────────────────────────────────────────┐
│ Layer 1: prep_host.sh (one-time host setup)                 │
│  Steps: snapd → LXD → OpenTofu → Ansible → SSH key → init  │
│  Result: Host is ready to create lab environments           │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: deploy_microcloud.sh (creates a full environment)  │
│  Phase A — Detect: finds LXD network + storage pool on host │
│  Phase B — Size: auto-computes node resources from host     │
│            capacity OR uses explicit --node-cpu etc.         │
│  Phase C — Provision (OpenTofu):                            │
│    • Creates LXD VMs (Ubuntu 24.04 virtual machines)        │
│    • Creates an OVN uplink bridge (IP-free, for MicroOVN)   │
│    • Creates per-node Ceph block volumes                    │
│    • Attaches eth1 (OVN uplink) + ceph-disk to each VM     │
│    • Injects SSH key via cloud-init                         │
│    • Generates Ansible inventory file                       │
│  Phase D — Bootstrap (Ansible playbook microcloud.yml):     │
│    • Waits for VMs to boot                                  │
│    • Installs snaps: microcloud, microceph, microovn, lxd   │
│    • Auto-detects OVN uplink interface (no-IP iface)        │
│    • Auto-detects Ceph disk (non-root block device)         │
│    • Generates MicroCloud preseed YAML                      │
│    • Runs `microcloud preseed` on all nodes (cluster init)  │
│    • Creates OVN UPLINK physical network                    │
│    • Creates OVN default logical network                    │
│    • Verifies cluster formation                             │
│  Phase C and D ALWAYS run sequentially — there is currently │
│  no flag to run only Phase C without Phase D.               │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: list_microcloud_environments.sh                    │
│  Queries tofu workspaces + lxc list to show active envs     │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: scale_microcloud.sh                                │
│  Re-runs deploy_microcloud.sh with higher --nodes count     │
│  on the same workspace (OpenTofu handles the diff)          │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 4b: add_cluster_node.sh (live cluster expansion)      │
│  Phase 1 — OpenTofu apply with incremented node count       │
│  Phase 2 — Ansible prepares ONLY the new nodes (snaps)      │
│  Phase 3 — `microcloud add` preseed to join new nodes live  │
│  Phase 4 — verify_cluster_health to confirm success         │
│  This is different from scale_environment: scale re-deploys │
│  via deploy script; add_cluster_node expands a live cluster. │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 4c: verify_cluster_health.sh                          │
│  Runs cluster list on all 4 services from the initiator:    │
│  microcloud, lxc, microceph, microovn cluster list          │
│  Also shows storage pools and networks.                     │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 5: cleanup_microcloud.sh                              │
│  Runs `tofu destroy` on the workspace → removes VMs,       │
│  networks, volumes. Then deletes workspace + inventory file.│
└─────────────────────────────────────────────────────────────┘

WHAT THIS MEANS FOR USER REQUESTS:
- "Deploy without installing snaps" → NOT possible with current scripts.
  Phase D (Ansible) always runs after Phase C. Be honest about this limitation.
- "Just create VMs" → Same answer: the script has no --skip-ansible flag.
  Suggest: deploy normally, then students can `snap remove` + re-install manually.
- "Use different snap channels" → NOT configurable via CLI flags. Channels are
  hardcoded in playbooks/microcloud.yml. Mention user can edit the playbook.
- "Custom preseed" → NOT exposed via CLI. Preseed is auto-generated by Ansible.
- "Change network after deploy" → NOT an automated operation. Guide manually.
- "Add nodes to existing cluster" → Use add_cluster_node tool, NOT scale_environment.
  add_cluster_node provisions VMs AND joins them to the live cluster via `microcloud add`.
  scale_environment re-runs the full deploy script and may not work on live clusters.
- "Check if cluster is healthy" → Use verify_cluster_health tool.
- "More Ceph disks per node" → Use ceph_disks_per_node parameter in deploy_microcloud.
- "Add local fast storage" → Use local_disk_gib >= 10 in deploy_microcloud.
- If a user asks for something the pipeline can't do, explain which layer
  handles it, why it's coupled, and suggest the closest achievable alternative.

IMPORTANT: Only pass parameters that actually exist in the tool definitions.
The orchestrator filters out unknown parameters, but you should never invent
parameters in the first place. Reason about what's possible from the architecture
above, not by guessing CLI flags.

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
0. If user asks for lifecycle actions (list/delete/scale/add-nodes/health-check), execute those tools directly.
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
- If user asks to add nodes to a running cluster, call add_cluster_node.
- If user asks to scale (re-deploy with more nodes), call scale_environment.
- If user asks to check cluster health/status, call verify_cluster_health.
- For scale/delete/add requests, gather or confirm the target workspace first.
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
        lifecycle_tools = {"list_environments", "delete_environment", "scale_environment", "add_cluster_node", "verify_cluster_health"}
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
