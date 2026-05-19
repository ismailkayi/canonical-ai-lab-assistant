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
from lab_ai_assistant.tools import validate_tool_parameters
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

            # Deployment requires explicit user confirmation before execution
            if ai_response.get("needs_confirmation"):
                confirmation_prompt = ai_response.get("confirmation_prompt", "Shall I proceed?")
                prefix = self._compose_user_facing_response(message, reasoning)
                if prefix:
                    return f"__CONFIRM__:{prefix}\n\n{confirmation_prompt}"
                return f"__CONFIRM__:{confirmation_prompt}"

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

            tool_result_for_ai = self._prepare_tool_result_for_ai(action, tool_result)

            # Feed result back to AI for synthesis (closes the loop)
            self.ui.print_phase("analyzing", "Processing tool results...")
            ai_response = self.ai_engine.feed_tool_result(action, tool_result_for_ai)

        # Fallback: return whatever the AI last said
        return self._compose_user_facing_response(
            ai_response.get("message") or ai_response.get("content", "Reached maximum reasoning rounds."),
            ai_response.get("reasoning", ""),
        )

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
        commands = {
            "cpu_cores": "nproc 2>/dev/null || echo unknown",
            "ram_mb": "awk '/MemTotal:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo unknown",
            "disks": "lsblk -dn -o NAME,SIZE,TYPE | awk '$3==\"disk\" {print $1\":\"$2}' | paste -sd ', ' -",
            "lxd_networks": "lxc network list --format csv 2>/dev/null | awk -F',' '{print $1}' | paste -sd ', ' -",
            "lxd_storage_pools": "lxc storage list --format csv 2>/dev/null | awk -F',' '{print $1}' | paste -sd ', ' -",
        }

        results: dict[str, str] = {}
        for key, cmd in commands.items():
            try:
                res = subprocess.run(
                    ["bash", "-lc", cmd],
                    capture_output=True,
                    text=True,
                    cwd=self.config.repo_root,
                    timeout=10,
                )
                out = (res.stdout or "").strip()
                results[key] = out if out else "unknown"
            except Exception:
                results[key] = "unknown"

        return (
            "Host environment snapshot\n"
            "  Deployment mode : nested-lxd-lab (OpenTofu creates MicroCloud VMs)\n"
            "  Ceph disk model : per-node virtual block volumes are provisioned automatically\n"
            f"  CPU cores        : {results.get('cpu_cores', 'unknown')}\n"
            f"  RAM              : {results.get('ram_mb', 'unknown')} MB\n"
            f"  Disk devices     : {results.get('disks', 'unknown')}\n"
            f"  LXD networks     : {results.get('lxd_networks', 'unknown')}\n"
            f"  LXD storage pool : {results.get('lxd_storage_pools', 'unknown')}"
        )

    # Whitelist of parameters each script actually accepts.
    # Any parameter NOT in this map will be silently dropped to prevent
    # the AI from hallucinating options and crashing the scripts.
    _SCRIPT_ACCEPTED_PARAMS: dict[str, set[str]] = {
        "prep_host": set(),
        "install_inference_snap": {"engine"},
        "deploy_microcloud": {
            "scenario", "nodes", "sizing_tier", "node_cpu", "node_memory_mb",
            "root_disk_gib", "ceph_disk_gib", "user_prefix", "ssh_key",
            "network_interface", "ovn_uplink_interface", "ceph_osd_disk",
        },
        "delete_environment": {"workspace"},
        "list_environments": set(),
        "scale_environment": {
            "workspace", "target_nodes", "sizing_tier", "node_cpu",
            "node_memory_mb", "root_disk_gib", "ceph_disk_gib",
        },
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

            if action in ("deploy_microcloud", "delete_environment", "scale_environment"):
                cmd.append("--auto-approve")

            logger.info(f"Running: {' '.join(cmd)}")
            capture_only = (action in ("deploy_microcloud", "delete_environment", "scale_environment"))
            output_lines: list[str] = []
            status_label = {
                "deploy_microcloud": "Deployment",
                "delete_environment": "Cleanup",
                "scale_environment": "Scaling",
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
                    heartbeat_every_sec = 15
                    start_ts = time.monotonic()
                    last_heartbeat_ts = start_ts
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

                        now = time.monotonic()
                        if proc.poll() is None and (now - last_heartbeat_ts) >= heartbeat_every_sec:
                            elapsed = int(now - start_ts)
                            self.ui.print_operation_progress(
                                status_label, f"still running... {elapsed}s elapsed"
                            )
                            last_heartbeat_ts = now

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

        if action in ("deploy_microcloud", "scale_environment") and len(tool_result) > deploy_tail_chars:
            return (
                "Operation finished. Full logs were streamed to the terminal and omitted for context safety.\n"
                f"Tail ({deploy_tail_chars} chars):\n{tool_result[-deploy_tail_chars:]}"
            )

        if len(tool_result) > max_chars:
            head_chars = max_chars // 2
            tail_chars = max_chars - head_chars
            return (
                f"Tool output truncated for context safety (original length: {len(tool_result)} chars).\n"
                f"Head ({head_chars} chars):\n{tool_result[:head_chars]}\n\n"
                f"Tail ({tail_chars} chars):\n{tool_result[-tail_chars:]}"
            )

        return tool_result

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
