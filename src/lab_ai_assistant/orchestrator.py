"""Lab orchestration and execution layer for custom-topology MicroCloud workflows."""

import json
import logging
import re
import select
import subprocess
import textwrap
import time
from datetime import datetime
from typing import Any

from lab_ai_assistant.ai_engine import AIEngine
from lab_ai_assistant.config import Config
from lab_ai_assistant.doc_fetcher import DocFetcher
from lab_ai_assistant.sizing import SizingAdvisor, SizingTier
from lab_ai_assistant.tools import get_tool_definitions, validate_tool_parameters
from lab_ai_assistant.ui import ChatUI

logger = logging.getLogger(__name__)

UI_WIDTH = 78


class LabOrchestrator:
    """Main orchestration layer for MicroCloud lab automation."""

    def __init__(self, config: Config):
        self.config = config
        self.ai_engine = AIEngine(config)
        self.sizing_advisor = SizingAdvisor()
        self.doc_fetcher = DocFetcher(cache_dir=config.state_dir)
        self.deployment_history = []
        self.ui = ChatUI()
        # Cache for host-state grounding so we don't re-shell out every turn.
        self._host_state_cache: dict[str, Any] | None = None
        self._host_state_cache_ts: float = 0.0
        self._host_state_ttl: float = 60.0
        self._load_history()

    def bootstrap_host(self) -> str:
        """Prepare the host and install the inference snap.

        Streams output live so the user can see progress and respond to sudo prompts.
        """
        for script in [self.config.prep_host_script, self.config.install_inference_script]:
            if not script.exists():
                print(f"[warn] Script not found, skipping: {script}")
                continue
            print(f"\n--- {script.name} ---")
            result = subprocess.run(
                ["bash", str(script)],
                cwd=self.config.repo_root,
                # No capture_output — output goes directly to the terminal
            )
            if result.returncode != 0:
                raise RuntimeError(f"{script.name} failed (exit {result.returncode})")
        return "Bootstrap complete. Run 'lab-ai check' to verify the inference service."

    def start_chat(self):
        """Start interactive chat session."""
        if not self.ai_engine.is_available():
            raise RuntimeError(
                f"Inference engine not available at {self.config.inference_host}\n"
                "Please verify the snap service is running and INFERENCE_HOST points to the correct endpoint."
            )

        self.ui.print_welcome()

        while True:
            try:
                user_input = self.ui.get_user_input()
                if not user_input:
                    continue
                if user_input.lower() == "quit":
                    self.ui.print_ai_status("Session ended. Goodbye!")
                    break
                if user_input.lower() == "help":
                    self.ui.print_help()
                    continue
                if user_input.lower().startswith("sizing"):
                    self.ui.print_ai_response(self.sizing_advisor.describe_tiers())
                    continue

                with self.ui.thinking_indicator("AI is analyzing your request"):
                    response = self._process_user_input(user_input)

                self._print_assistant_response(response)

            except KeyboardInterrupt:
                self.ui.print_ai_status("Session ended. Goodbye!")
                break
            except Exception as exc:
                logger.error(f"Error in chat loop: {exc}")
                self.ui.print_error(str(exc))

    def _process_user_input(self, user_message: str) -> str:
        """Process user input through the agentic loop.

        The loop runs until the AI returns no tool call (final answer) or
        the maximum number of tool rounds is reached.
        """
        MAX_TOOL_ROUNDS = 5

        # Ground the model in real host capacity + active environments before it
        # reasons. Cached for a short TTL so this is cheap on every turn.
        self._refresh_ai_environment_context()

        ai_response = self.ai_engine.chat(user_message)

        for _round in range(MAX_TOOL_ROUNDS):
            if ai_response.get("error"):
                return ai_response.get("content", "An error occurred")

            action = ai_response.get("action")
            message = ai_response.get("message") or ai_response.get("content", "")
            reasoning = ai_response.get("reasoning", "")

            # No tool call → this is the final user-facing answer
            if not action:
                return self._compose_user_facing_response(message, reasoning)

            # Show AI's intermediate plan/reasoning
            if message or reasoning:
                plan = self._compose_user_facing_response(message, reasoning)
                if plan:
                    self.ui.print_ai_plan(plan)

            parameters = ai_response.get("parameters", {})
            action = self._resolve_tool_action(action, message, reasoning, parameters, user_message)

            # Deployment requires explicit user confirmation before execution
            if ai_response.get("needs_confirmation"):
                confirmation_prompt = ai_response.get("confirmation_prompt", "Shall I proceed?")
                prefix = self._compose_user_facing_response(message, reasoning)
                if prefix:
                    return f"__CONFIRM__:{prefix}\n\n{confirmation_prompt}"
                return f"__CONFIRM__:{confirmation_prompt}"

            if not action:
                return self._compose_user_facing_response(message, reasoning)

            # Show tool execution in the UI
            self.ui.print_tool_call(action)

            # Execute the tool
            tool_result = self._handle_local_tool(action, parameters)
            if tool_result is None:
                is_valid, error_msg = validate_tool_parameters(action, parameters)
                if not is_valid:
                    return f"I still need more information: {error_msg}"
                logger.info(f"Executing action: {action} params={parameters}")
                tool_result = self._execute_action(action, parameters)
                self._record_deployment(action, parameters, tool_result)

            if self._is_failed_tool_result(tool_result):
                return self._compose_failed_tool_response(action, tool_result)

            tool_result_for_ai = self._prepare_tool_result_for_ai(action, tool_result)

            # Feed result back to AI for synthesis (closes the loop)
            self.ui.print_phase("analyzing", "Processing tool results...")
            ai_response = self.ai_engine.feed_tool_result(action, tool_result_for_ai)

        # Fallback: return whatever the AI last said
        return self._compose_user_facing_response(
            ai_response.get("message") or ai_response.get("content", "Reached maximum reasoning rounds."),
            ai_response.get("reasoning", ""),
        )

    def _refresh_ai_environment_context(self) -> None:
        """Push a fresh host-grounded context snapshot into the AI engine.

        Failures here must never block the chat, so we degrade silently.
        """
        try:
            state = self._collect_host_state()
            self.ai_engine.set_environment_context(self._format_host_state_for_prompt(state))
        except Exception as exc:
            logger.debug(f"Could not refresh environment context: {exc}")

    def _is_failed_tool_result(self, tool_result: str) -> bool:
        """Detect hard failures from script-backed tools."""
        if not tool_result:
            return False
        return tool_result.startswith("Script failed") or tool_result.startswith("Error:") or "Traceback" in tool_result

    def _compose_failed_tool_response(self, action: str, tool_result: str) -> str:
        """Return a deterministic failure summary so we never imply background work continues."""
        summary = self._truncate_text(tool_result.strip(), 1200)
        if action in ("deploy_microcloud", "delete_environment", "scale_environment", "add_cluster_node", "verify_cluster_health"):
            return (
                f"{action.replace('_', ' ').capitalize()} failed. No background work is running.\n\n"
                f"Last error:\n{summary}"
            )
        return f"Operation failed.\n\nLast error:\n{summary}"

    def _resolve_tool_action(
        self,
        action: str | None,
        message: str,
        reasoning: str,
        parameters: dict[str, Any],
        user_input: str,
    ) -> str | None:
        """Normalize generic tool labels back to a concrete tool name when possible."""
        known_tools = {tool["name"] for tool in get_tool_definitions().get("tools", []) if tool.get("name")}
        if action in known_tools:
            return action

        text = "\n".join(part for part in (user_input, message, reasoning) if part).lower()

        if any(keyword in text for keyword in ("delete", "cleanup", "clean up", "destroy", "remove")):
            return "delete_environment"
        if any(keyword in text for keyword in ("list", "show", "enumerate")) and any(
            keyword in text for keyword in ("lab", "environment", "environments", "labs")
        ):
            return "list_environments"
        if any(keyword in text for keyword in ("health", "status", "check", "verify")):
            return "verify_cluster_health"
        if any(keyword in text for keyword in ("add node", "add nodes", "join node", "join nodes", "expand cluster")):
            return "add_cluster_node"
        if any(keyword in text for keyword in ("scale", "resize", "increase nodes", "more nodes", "re-deploy", "redeploy")):
            return "scale_environment"

        workspace_hint = parameters.get("workspace") if isinstance(parameters, dict) else None
        if action == "tool_call" and isinstance(workspace_hint, str) and workspace_hint:
            return "delete_environment"

        return action if action in known_tools else None

    def _compose_user_facing_response(self, message: str, reasoning: str) -> str:
        """Build a concise, readable assistant response for terminal UX."""
        clean_message = self._normalize_assistant_text(message)
        clean_reasoning = self._normalize_assistant_text(reasoning)

        if clean_message:
            return clean_message

        if clean_reasoning:
            # Keep reasoning as a short fallback when message is missing.
            short_reasoning = self._truncate_text(clean_reasoning, 320)
            return f"Plan: {short_reasoning}"

        return (
            "I received an empty response from the inference backend. "
            "Please retry your request."
        )

    def _normalize_assistant_text(self, text: str) -> str:
        """Remove noisy fragments and repeated blocks from model output."""
        if not text:
            return ""

        cleaned = text.replace("\r\n", "\n").strip()
        cleaned = re.sub(r"<tool_code>\s*\{.*?\}\s*</tool_code>", "", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
        cleaned = cleaned.replace("```", "")
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        # Remove immediate duplicate paragraphs that frequently appear with local models.
        blocks = [b.strip() for b in re.split(r"\n\s*\n", cleaned) if b.strip()]
        deduped: list[str] = []
        seen_recent: list[str] = []
        for block in blocks:
            key = re.sub(r"\s+", " ", block.lower()).strip()
            if key in seen_recent:
                continue
            deduped.append(block)
            seen_recent.append(key)
            if len(seen_recent) > 12:
                # Keep memory bounded and focus on near-duplicates.
                seen_recent = seen_recent[-8:]

        return "\n\n".join(deduped).strip()

    def _format_for_terminal(self, text: str) -> str:
        """Render readable terminal output with wrapped text and aligned tables."""
        if not text:
            return ""

        rendered = self._render_markdown_tables(text)

        output_lines: list[str] = []
        in_table = False
        for raw_line in rendered.splitlines():
            line = self._strip_markdown_noise(raw_line.rstrip())
            if line.startswith("+") and line.endswith("+"):
                in_table = True
                output_lines.append(line)
                continue
            if in_table and line.startswith("|") and line.endswith("|"):
                output_lines.append(line)
                continue
            if in_table and line.strip() == "":
                in_table = False
                output_lines.append("")
                continue
            if in_table and not (line.startswith("|") and line.endswith("|")):
                in_table = False

            if not line:
                output_lines.append("")
                continue

            if self._is_list_line(line):
                output_lines.append(textwrap.fill(line, width=UI_WIDTH, subsequent_indent="  "))
            else:
                output_lines.append(textwrap.fill(line, width=UI_WIDTH))

        compacted = self._compact_blank_lines(output_lines)
        return "\n".join(compacted).strip()

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    def _print_assistant_response(self, response: str) -> None:
        """Print the final assistant response with the rich UI."""
        if not response:
            self.ui.print_ai_response("")
            return

        # Handle confirmation flow
        if response.startswith("__CONFIRM__:"):
            parts = response[len("__CONFIRM__:"):].rsplit("\n\n", 1)
            message = parts[0] if len(parts) > 1 else ""
            prompt = parts[-1] if parts else "Shall I proceed?"
            self.ui.print_confirmation_prompt(message, prompt)
            return

        cleaned = self._normalize_assistant_text(response)
        if not cleaned:
            self.ui.print_ai_response("")
            return

        cleaned = self._truncate_for_display(cleaned)
        formatted = self._format_for_terminal(cleaned)
        self.ui.print_ai_response(formatted)

    def _print_section_header(self, title: str) -> None:
        """Legacy header — now delegates to UI phase display."""
        self.ui.print_phase("executing", title)

    def _print_section_footer(self) -> None:
        pass

    def _strip_markdown_noise(self, line: str) -> str:
        """Remove visual markdown artifacts that hurt terminal readability."""
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = line.replace("**", "")
        line = line.replace("__", "")
        line = line.replace("`", "")
        return line

    def _is_list_line(self, line: str) -> bool:
        stripped = line.lstrip()
        return bool(re.match(r"^(-|\*|\d+\.)\s+", stripped))

    def _compact_blank_lines(self, lines: list[str]) -> list[str]:
        compacted: list[str] = []
        blank_streak = 0
        for line in lines:
            if line.strip() == "":
                blank_streak += 1
                if blank_streak > 1:
                    continue
            else:
                blank_streak = 0
            compacted.append(line)
        return compacted

    def _truncate_for_display(self, text: str) -> str:
        """Keep terminal output scannable by trimming very long responses."""
        max_lines = 36
        lines = text.splitlines()
        if len(lines) <= max_lines:
            return text

        kept = lines[:max_lines]
        kept.append("")
        kept.append("[Output shortened for readability. Ask: 'give me full details' if needed.]")
        return "\n".join(kept)

    def _render_markdown_tables(self, text: str) -> str:
        """Convert markdown tables to aligned ASCII tables for terminal display."""
        lines = text.splitlines()
        rendered: list[str] = []
        idx = 0

        while idx < len(lines):
            if "|" not in lines[idx]:
                rendered.append(lines[idx])
                idx += 1
                continue

            start = idx
            while idx < len(lines) and "|" in lines[idx]:
                idx += 1

            block = lines[start:idx]
            if self._looks_like_markdown_table(block):
                rendered.append(self._render_markdown_table_block(block))
            else:
                rendered.extend(block)

        return "\n".join(rendered)

    def _looks_like_markdown_table(self, lines: list[str]) -> bool:
        if len(lines) < 2:
            return False
        separator = lines[1].strip()
        return bool(re.match(r"^\|?\s*[:\-\|\s]+\|?\s*$", separator))

    def _render_markdown_table_block(self, lines: list[str]) -> str:
        rows: list[list[str]] = []
        for line in lines:
            if re.match(r"^\|?\s*[:\-\|\s]+\|?\s*$", line.strip()):
                continue
            stripped = line.strip().strip("|")
            cells = [self._strip_markdown_noise(cell.strip()) for cell in stripped.split("|")]
            rows.append(cells)

        if not rows:
            return ""

        col_count = max(len(r) for r in rows)
        for row in rows:
            if len(row) < col_count:
                row.extend([""] * (col_count - len(row)))

        widths = [max(len(row[col]) for row in rows) for col in range(col_count)]

        def render_row(row: list[str]) -> str:
            cols = [f" {row[i].ljust(widths[i]) } " for i in range(col_count)]
            return "|" + "|".join(cols) + "|"

        sep = "+" + "+".join(["-" * (w + 2) for w in widths]) + "+"
        output = [sep, render_row(rows[0]), sep]
        for row in rows[1:]:
            output.append(render_row(row))
        output.append(sep)
        return "\n".join(output)

    def _handle_local_tool(self, action: str, parameters: dict[str, Any]) -> str | None:
        """Execute tools that do not require external scripts."""
        if action == "inspect_host_environment":
            return self._inspect_host_environment()

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
            scenario_name = parameters.get("scenario", "custom")
            nodes = parameters.get("nodes")
            workload = parameters.get("workload_description", "")
            tier_str = parameters.get("tier")
            tier = SizingTier(tier_str) if tier_str else None

            # Prefer host-aware sizing: it mirrors deploy_microcloud.sh exactly, so
            # the numbers shown to the user are what will actually be provisioned.
            host_state = self._collect_host_state()
            if host_state.get("cpu_cores"):
                profile = self._tier_to_profile(tier_str, workload)
                host_sizing = self.sizing_advisor.host_aware_size(
                    host_state=host_state,
                    nodes=int(nodes) if nodes else 3,
                    profile=profile,
                )
                return host_sizing.summary()

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
        state = self._collect_host_state(force=True)
        return self._format_host_state_report(state)

    def _run_host_cmd(self, cmd: str, timeout: int = 10) -> str:
        """Run a read-only host probe and return trimmed stdout (or '' on failure)."""
        try:
            res = subprocess.run(
                ["bash", "-lc", cmd],
                capture_output=True,
                text=True,
                cwd=self.config.repo_root,
                timeout=timeout,
            )
            return (res.stdout or "").strip()
        except Exception:
            return ""

    def _collect_host_state(self, force: bool = False) -> dict[str, Any]:
        """Collect rich, structured host facts used for AI grounding.

        Cached for a short TTL so we can inject it into the prompt every turn
        without repeatedly shelling out. Set force=True for an explicit refresh
        (e.g. the inspect_host_environment tool).
        """
        now = time.time()
        if (
            not force
            and self._host_state_cache is not None
            and (now - self._host_state_cache_ts) < self._host_state_ttl
        ):
            return self._host_state_cache

        cpu_cores = self._parse_int(self._run_host_cmd("nproc 2>/dev/null"), default=0)
        ram_total_mb = self._parse_int(
            self._run_host_cmd("awk '/MemTotal:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null"),
            default=0,
        )
        ram_available_mb = self._parse_int(
            self._run_host_cmd("awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null"),
            default=0,
        )
        disks = self._run_host_cmd(
            "lsblk -dn -o NAME,SIZE,TYPE | awk '$3==\"disk\" {print $1\":\"$2}' | paste -sd ', ' -"
        )
        networks_raw = self._run_host_cmd(
            "lxc network list --format csv 2>/dev/null | awk -F',' '{print $1}' | paste -sd ', ' -"
        )
        pools_raw = self._run_host_cmd(
            "lxc storage list --format csv 2>/dev/null | awk -F',' '{print $1}' | paste -sd ', ' -"
        )
        lxd_version = self._run_host_cmd(
            "lxc version 2>/dev/null | awk '/Server version:/ {print $3; exit}'"
        )

        pools = [p.strip() for p in pools_raw.split(",") if p.strip()] if pools_raw else []
        primary_pool = pools[0] if pools else "default"
        storage_available_gib = self._get_pool_available_gib(primary_pool)
        environments = self._collect_environment_usage()

        consumed_cpu = sum(env.get("total_cpu", 0) for env in environments)
        consumed_ram_gb = sum(env.get("total_ram_gb", 0) for env in environments)

        state: dict[str, Any] = {
            "cpu_cores": cpu_cores,
            "ram_total_mb": ram_total_mb,
            "ram_available_mb": ram_available_mb,
            "disks": disks or "unknown",
            "lxd_networks": networks_raw or "unknown",
            "lxd_storage_pools": pools_raw or "unknown",
            "primary_pool": primary_pool,
            "storage_available_gib": storage_available_gib,
            "lxd_version": lxd_version or "unknown",
            "environments": environments,
            "consumed_cpu": consumed_cpu,
            "consumed_ram_gb": consumed_ram_gb,
        }

        self._host_state_cache = state
        self._host_state_cache_ts = now
        return state

    def _get_pool_available_gib(self, pool: str) -> int:
        """Best-effort free space (GiB) in an LXD storage pool, with df fallback."""
        info = self._run_host_cmd(f"lxc storage info {pool} 2>/dev/null")
        line = ""
        for raw in info.splitlines():
            if "Space available:" in raw:
                line = raw.split("Space available:", 1)[1].strip()
                break
        if line:
            num_match = re.search(r"[0-9]+(?:\.[0-9]+)?", line)
            unit_match = re.search(r"[A-Za-z]+", line)
            if num_match:
                num = float(num_match.group(0))
                unit = (unit_match.group(0) if unit_match else "GiB").upper()
                if unit.startswith("T"):
                    return int(num * 1024)
                if unit.startswith("G"):
                    return int(num)
                if unit.startswith("M"):
                    return int(num / 1024)
        df_out = self._run_host_cmd("df -BG . 2>/dev/null | awk 'NR==2 {gsub(/G/,\"\",$4); print $4}'")
        return self._parse_int(df_out, default=0)

    def _collect_environment_usage(self) -> list[dict[str, Any]]:
        """List active lab environments and their resource footprint via lxc.

        Lab VMs follow the naming pattern '<prefix>-node-<n>'. We group by prefix,
        count nodes, and sum CPU/RAM limits so the AI knows real free headroom.
        """
        csv_out = self._run_host_cmd(
            "lxc list --format csv -c n,s,config:limits.cpu,config:limits.memory 2>/dev/null"
        )
        if not csv_out:
            return []

        groups: dict[str, dict[str, Any]] = {}
        for row in csv_out.splitlines():
            cols = [c.strip() for c in row.split(",")]
            if not cols or not cols[0]:
                continue
            name = cols[0]
            match = re.match(r"^(?P<prefix>.+)-node-\d+$", name)
            if not match:
                continue
            prefix = match.group("prefix")
            cpu = self._parse_int(cols[2] if len(cols) > 2 else "", default=0)
            ram_gb = self._parse_memory_gb(cols[3] if len(cols) > 3 else "")

            env = groups.setdefault(
                prefix,
                {"name": prefix, "nodes": 0, "node_cpu": cpu, "node_ram_gb": ram_gb,
                 "total_cpu": 0, "total_ram_gb": 0},
            )
            env["nodes"] += 1
            env["total_cpu"] += cpu
            env["total_ram_gb"] += ram_gb

        return list(groups.values())

    @staticmethod
    def _parse_int(value: str, default: int = 0) -> int:
        match = re.search(r"-?\d+", value or "")
        return int(match.group(0)) if match else default

    @staticmethod
    def _parse_memory_gb(value: str) -> int:
        """Parse an LXD memory limit like '8GiB' / '8192MiB' into whole GB."""
        if not value:
            return 0
        num_match = re.search(r"[0-9]+(?:\.[0-9]+)?", value)
        if not num_match:
            return 0
        num = float(num_match.group(0))
        unit = (re.search(r"[A-Za-z]+", value).group(0) if re.search(r"[A-Za-z]+", value) else "GiB").upper()
        if unit.startswith("T"):
            return int(num * 1024)
        if unit.startswith("M"):
            return int(num / 1024)
        if unit.startswith("K"):
            return int(num / (1024 * 1024))
        return int(num)

    @staticmethod
    def _tier_to_profile(tier_str: str | None, workload: str = "") -> str:
        """Map a requested tier/workload to a host-aware sizing profile."""
        text = f"{tier_str or ''} {workload or ''}".lower()
        if any(k in text for k in ("minimal", "poc", "proof of concept", "dev", "sandbox", "conservative")):
            return "conservative"
        if any(k in text for k in ("large", "production", "prod", "performance", "ha", "high availability", "enterprise")):
            return "performance"
        return "balanced"

    def _format_host_state_report(self, state: dict[str, Any]) -> str:
        """Human-readable host snapshot returned by inspect_host_environment."""
        envs = state.get("environments", [])
        if envs:
            env_lines = "\n".join(
                f"    - {e['name']}: {e['nodes']} nodes "
                f"({e['node_cpu']} vCPU / {e['node_ram_gb']} GB each, "
                f"total {e['total_cpu']} vCPU / {e['total_ram_gb']} GB)"
                for e in envs
            )
        else:
            env_lines = "    (none)"

        ram_total_gb = round(state.get("ram_total_mb", 0) / 1024, 1)
        ram_avail_gb = round(state.get("ram_available_mb", 0) / 1024, 1)
        return (
            "Host environment snapshot\n"
            "  Deployment mode : nested-lxd-lab (OpenTofu creates MicroCloud VMs)\n"
            "  Ceph disk model : per-node virtual block volumes are provisioned automatically\n"
            f"  CPU cores        : {state.get('cpu_cores', 'unknown')}\n"
            f"  RAM              : {ram_total_gb} GB total ({ram_avail_gb} GB available)\n"
            f"  Disk devices     : {state.get('disks', 'unknown')}\n"
            f"  LXD version      : {state.get('lxd_version', 'unknown')}\n"
            f"  LXD networks     : {state.get('lxd_networks', 'unknown')}\n"
            f"  LXD storage pool : {state.get('lxd_storage_pools', 'unknown')} "
            f"(~{state.get('storage_available_gib', 0)} GiB free in '{state.get('primary_pool', 'default')}')\n"
            "  Active lab environments:\n"
            f"{env_lines}"
        )

    def _format_host_state_for_prompt(self, state: dict[str, Any]) -> str:
        """Compact, capacity-focused context injected into the AI system prompt."""
        cpu = state.get("cpu_cores", 0)
        ram_total_gb = round(state.get("ram_total_mb", 0) / 1024)
        ram_avail_gb = round(state.get("ram_available_mb", 0) / 1024)
        pool = state.get("primary_pool", "default")
        free_storage = state.get("storage_available_gib", 0)
        consumed_cpu = state.get("consumed_cpu", 0)
        consumed_ram = state.get("consumed_ram_gb", 0)
        free_cpu = max(cpu - consumed_cpu, 0)
        free_ram = max(ram_avail_gb, 0)

        envs = state.get("environments", [])
        if envs:
            env_lines = "\n".join(
                f"    - {e['name']}: {e['nodes']} nodes "
                f"(~{e['total_cpu']} vCPU / {e['total_ram_gb']} GB)"
                for e in envs
            )
        else:
            env_lines = "    (none)"

        return (
            f"  Host capacity : {cpu} vCPU | {ram_total_gb} GB RAM total "
            f"({ram_avail_gb} GB available) | {free_storage} GiB free in pool '{pool}'\n"
            f"  LXD version   : {state.get('lxd_version', 'unknown')}\n"
            f"  LXD networks  : {state.get('lxd_networks', 'unknown')}\n"
            f"  Active lab environments (already consuming resources):\n"
            f"{env_lines}\n"
            f"  Approx free headroom for new labs: ~{free_cpu} vCPU / ~{free_ram} GB RAM / "
            f"~{free_storage} GiB storage"
        )

    # Whitelist of parameters each script actually accepts.
    # Any parameter NOT in this map will be silently dropped to prevent
    # the AI from hallucinating options and crashing the scripts.
    _SCRIPT_ACCEPTED_PARAMS: dict[str, set[str]] = {
        "prep_host": set(),
        "install_inference_snap": {"engine"},
        "deploy_microcloud": {
            "scenario", "nodes", "sizing_tier", "node_cpu", "node_memory_mb",
            "root_disk_gib", "ceph_disk_gib", "ceph_disks_per_node", "local_disk_gib",
            "user_prefix", "ssh_key", "network_interface", "ovn_uplink_interface", "ceph_osd_disk",
        },
        "delete_environment": {"workspace"},
        "list_environments": set(),
        "scale_environment": {
            "workspace", "target_nodes", "sizing_tier", "node_cpu",
            "node_memory_mb", "root_disk_gib", "ceph_disk_gib",
            "ceph_disks_per_node", "local_disk_gib",
        },
        "add_cluster_node": {
            "workspace", "add_nodes", "sizing_tier", "node_cpu",
            "node_memory_mb", "root_disk_gib", "ceph_disk_gib",
            "ceph_disks_per_node", "local_disk_gib",
        },
        "verify_cluster_health": {"workspace"},
    }

    def _execute_action(self, action: str, parameters: dict[str, Any]) -> str:
        """Execute script-backed actions."""
        try:
            script_map = {
                "prep_host": self.config.prep_host_script,
                "install_inference_snap": self.config.install_inference_script,
                "deploy_microcloud": self.config.deploy_microcloud_script,
                "delete_environment": self.config.cleanup_microcloud_script,
                "list_environments": self.config.list_environments_script,
                "scale_environment": self.config.scale_microcloud_script,
                "add_cluster_node": self.config.add_cluster_node_script,
                "verify_cluster_health": self.config.verify_cluster_health_script,
            }

            script_path = script_map.get(action)
            if not script_path:
                return f"No script mapped for action: {action}"

            accepted = self._SCRIPT_ACCEPTED_PARAMS.get(action, set())
            cmd = ["bash", str(script_path)]
            for key, value in parameters.items():
                if value is None or key not in accepted:
                    continue
                # Guard: model sometimes passes full workspace name as user_prefix.
                # deploy_microcloud.sh appends _microcloud itself, so strip it.
                if key == "user_prefix" and isinstance(value, str):
                    value = value.removesuffix("_microcloud")
                cmd.append(f"--{key.replace('_', '-')}={value}")

            if action in ("deploy_microcloud", "delete_environment", "scale_environment", "add_cluster_node"):
                cmd.append("--auto-approve")

            logger.info(f"Running: {' '.join(cmd)}")
            capture_only = (action in ("deploy_microcloud", "delete_environment", "scale_environment", "add_cluster_node", "verify_cluster_health"))
            output_lines: list[str] = []
            status_label = {
                "deploy_microcloud": "Deployment",
                "delete_environment": "Cleanup",
                "scale_environment": "Scaling",
                "add_cluster_node": "Node Addition",
                "verify_cluster_health": "Health Check",
            }.get(action, "Operation")

            if capture_only:
                self.ui.print_operation_progress(status_label, "Started — streaming checkpoints...")

            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self.config.repo_root,
            ) as proc:
                assert proc.stdout is not None
                if not capture_only:
                    for line in proc.stdout:
                        print(line, end="", flush=True)
                        output_lines.append(line)
                else:
                    milestone_pattern = re.compile(
                        r"\[INFO\]|\[WARN\]|\[ERROR\]|\[SUCCESS\]|"
                        r"Apply complete|Destroy complete|PLAY RECAP|TASK \[",
                        flags=re.IGNORECASE,
                    )

                    while True:
                        ready, _, _ = select.select([proc.stdout], [], [], 1.0)
                        if ready:
                            line = proc.stdout.readline()
                            if line:
                                output_lines.append(line)
                                if milestone_pattern.search(line):
                                    self.ui.print_operation_progress(
                                        status_label, line.strip()
                                    )

                        if proc.poll() is not None:
                            break

                    tail = proc.stdout.read()
                    if tail:
                        output_lines.append(tail)

                proc.wait()
            if capture_only:
                self.ui.print_phase("done", f"{status_label} finished")

            output = "".join(output_lines)
            if proc.returncode != 0:
                return f"Script failed (exit {proc.returncode}):\n{output[-2000:]}"
            return output or "(no output)"

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

    def _prepare_tool_result_for_ai(self, action: str, tool_result: str) -> str:
        """Trim very large tool outputs before feeding them back to the model."""
        max_chars = 6000
        deploy_tail_chars = 2000

        if not tool_result:
            return "(no output)"

        if action in ("deploy_microcloud", "scale_environment"):
            access_details = self._extract_deployment_access_details(tool_result)
            if len(tool_result) > deploy_tail_chars:
                compact = (
                    "Operation finished. Full logs were streamed to the terminal and omitted for context safety.\n"
                    f"Tail ({deploy_tail_chars} chars):\n{tool_result[-deploy_tail_chars:]}"
                )
                if access_details:
                    return f"{access_details}\n\n{compact}"
                return compact
            if access_details:
                return f"{access_details}\n\n{tool_result}"

        if len(tool_result) > max_chars:
            head_chars = max_chars // 2
            tail_chars = max_chars - head_chars
            return (
                f"Tool output truncated for context safety (original length: {len(tool_result)} chars).\n"
                f"Head ({head_chars} chars):\n{tool_result[:head_chars]}\n\n"
                f"Tail ({tail_chars} chars):\n{tool_result[-tail_chars:]}"
            )

        return tool_result

    def _extract_deployment_access_details(self, tool_result: str) -> str:
        """Extract deterministic access details (IPs/UI/commands) from deploy script output."""
        if not tool_result:
            return ""

        env_match = re.search(r"^\s*Environment\s+(\S+)\s*$", tool_result, flags=re.MULTILINE)
        workspace = env_match.group(1) if env_match else ""

        node_rows = re.findall(
            r"^\s*(\S+-node-\d+)\s+(\d+\.\d+\.\d+\.\d+|N/A)\s+(https?://\S+|-)\s*$",
            tool_result,
            flags=re.MULTILINE,
        )

        if not workspace and not node_rows:
            return ""

        lines = ["Deployment access details:"]
        if workspace:
            lines.append(f"- Workspace: {workspace}")

        if node_rows:
            lines.append("- Nodes:")
            for node_name, ip, ui_url in node_rows:
                node_bits = [f"{node_name}"]
                node_bits.append(f"IP={ip}")
                if ui_url != "-":
                    node_bits.append(f"UI={ui_url}")
                lines.append(f"  - {', '.join(node_bits)}")

            first_node, first_ip, _ = node_rows[0]
            lines.append("- Quick access:")
            lines.append(f"  - LXD shell: lxc exec {first_node} -- bash")
            if first_ip != "N/A":
                lines.append(f"  - SSH: ssh ubuntu@{first_ip}")

        return "\n".join(lines)

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
        self.ui.print_help()
