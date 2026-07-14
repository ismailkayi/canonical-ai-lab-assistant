"""Evidence-based postcondition verification for deployed MicroCloud labs."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ServiceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    status: Literal["healthy", "partial", "unhealthy"]
    command_ok: bool
    expected_members: int | None = None
    observed_members: int | None = None
    detail: str = ""


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace: str
    status: Literal["healthy", "partial", "unhealthy"]
    expected_nodes: int
    expected_osds: int | None
    checks: tuple[ServiceCheck, ...]

    def as_tool_result(self) -> str:
        return self.model_dump_json(indent=2)


class ClusterVerifier:
    """Collect cluster observations and compare them with persisted deployment intent."""

    def __init__(self, terraform_dir: Path):
        self.terraform_dir = terraform_dir

    def workspace_exists(self, workspace: str) -> bool:
        """Return whether OpenTofu already has the exact workspace name."""
        try:
            result = subprocess.run(
                ["tofu", "workspace", "list"],
                cwd=self.terraform_dir,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        names = {line.replace("*", "").strip() for line in result.stdout.splitlines()}
        return workspace in names

    def verify(
        self,
        workspace: str,
        expected_nodes: int | None = None,
        expected_osds: int | None = None,
    ) -> VerificationReport:
        spec = self.deployment_spec(workspace)
        if expected_nodes is None:
            expected_nodes = int(spec.get("node_count", 0) or 0)
        if expected_osds is None:
            ceph_disks = spec.get("ceph_disks_per_node")
            expected_osds = (
                expected_nodes * int(ceph_disks)
                if expected_nodes and ceph_disks is not None
                else None
            )

        prefix = workspace.replace("_", "-")
        initiator = f"{prefix}-node-1"
        expected_names = {f"{prefix}-node-{index}" for index in range(1, expected_nodes + 1)}

        checks: list[ServiceCheck] = []
        commands = {
            "microcloud": ["microcloud", "cluster", "list"],
            "lxd": ["lxc", "cluster", "list"],
            "microceph": ["microceph", "cluster", "list"],
            "microovn": ["microovn", "cluster", "list"],
        }
        for name, command in commands.items():
            returncode, output = self._run_in_node(initiator, command)
            members = self._parse_member_statuses(output)
            observed_names = set(members) & expected_names
            observed = len(observed_names)
            missing = sorted(expected_names - observed_names)
            unexpected = sorted(set(members) - expected_names)
            non_online = sorted(
                member for member in observed_names if members.get(member) != "ONLINE"
            )
            if returncode != 0:
                status: Literal["healthy", "partial", "unhealthy"] = "unhealthy"
                detail = self._compact(output) or f"{name} command failed"
            elif expected_nodes == 0:
                status = "partial"
                detail = "Expected member count is unavailable from Terraform state."
            elif missing or unexpected:
                status = "partial"
                detail = (
                    f"Expected {expected_nodes} exact members, observed {observed}; "
                    f"missing={missing}, unexpected={unexpected}."
                )
            elif non_online:
                status = "partial"
                detail = f"Members not ONLINE: {', '.join(non_online)}."
            else:
                status = "healthy"
                detail = f"All {observed} expected members are present."
            checks.append(
                ServiceCheck(
                    name=name,
                    status=status,
                    command_ok=returncode == 0,
                    expected_members=expected_nodes or None,
                    observed_members=observed,
                    detail=detail,
                )
            )

        checks.append(self._check_ceph_health(initiator, expected_osds))
        overall = self._overall_status(checks)
        return VerificationReport(
            workspace=workspace,
            status=overall,
            expected_nodes=expected_nodes,
            expected_osds=expected_osds,
            checks=tuple(checks),
        )

    def workspace_state(self, workspace: str) -> dict[str, Any]:
        """Return exact Terraform state identity plus current persisted intent."""
        env = os.environ.copy()
        env["TF_WORKSPACE"] = workspace
        try:
            state_result = subprocess.run(
                ["tofu", "state", "pull"],
                cwd=self.terraform_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            nodes_result = subprocess.run(
                ["tofu", "output", "-json", "node_names"],
                cwd=self.terraform_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if state_result.returncode != 0 or nodes_result.returncode != 0:
            return {}
        try:
            state = json.loads(state_result.stdout)
            nodes = json.loads(nodes_result.stdout)
        except (TypeError, ValueError):
            return {}
        if not isinstance(state, dict) or not isinstance(nodes, list):
            return {}
        spec = self.deployment_spec(workspace)
        return {
            "workspace": workspace,
            "state_lineage": str(state.get("lineage", "")),
            "state_serial": int(state.get("serial", 0) or 0),
            "current_nodes": len(nodes),
            "storage_pool": str(spec.get("lxd_storage_pool", "unknown")),
        }

    def deployment_spec(self, workspace: str) -> dict[str, Any]:
        env = os.environ.copy()
        env["TF_WORKSPACE"] = workspace
        try:
            result = subprocess.run(
                ["tofu", "output", "-json", "deployment_spec"],
                cwd=self.terraform_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if result.returncode != 0:
            return {}
        try:
            parsed = json.loads(result.stdout)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _run_in_node(node: str, command: list[str]) -> tuple[int, str]:
        try:
            result = subprocess.run(
                ["lxc", "exec", node, "--", *command],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            return result.returncode, output
        except subprocess.TimeoutExpired:
            return 124, "Command timed out"
        except OSError as exc:
            return 127, str(exc)

    def _check_ceph_health(
        self,
        initiator: str,
        expected_osds: int | None,
    ) -> ServiceCheck:
        returncode, output = self._run_in_node(
            initiator,
            ["microceph.ceph", "-s"],
        )
        health_match = re.search(r"\bHEALTH_(OK|WARN|ERR)\b", output)
        health = f"HEALTH_{health_match.group(1)}" if health_match else "UNKNOWN"
        osd_match = re.search(
            r"^\s*osd:\s*(\d+)\s+osds?\b",
            output,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        observed_osds = int(osd_match.group(1)) if osd_match else None

        if returncode != 0 or health == "HEALTH_ERR":
            status: Literal["healthy", "partial", "unhealthy"] = "unhealthy"
        elif (
            health != "HEALTH_OK"
            or expected_osds is None
            or observed_osds is None
            or observed_osds != expected_osds
        ):
            status = "partial"
        else:
            status = "healthy"

        detail = f"health={health}, expected_osds={expected_osds}, observed_osds={observed_osds}"
        if returncode != 0:
            detail = f"{detail}; {self._compact(output)}"
        return ServiceCheck(
            name="ceph-health",
            status=status,
            command_ok=returncode == 0,
            detail=detail,
        )

    @staticmethod
    def _parse_member_statuses(output: str) -> dict[str, str]:
        """Parse exact first-column member names from cluster table rows."""
        members: dict[str, str] = {}
        member_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        status_pattern = re.compile(r"\b(ONLINE|OFFLINE|EVACUATED)\b", re.IGNORECASE)
        for line in output.splitlines():
            status_match = status_pattern.search(line)
            if not status_match:
                continue
            stripped = line.strip()
            if stripped.startswith(("|", "│")):
                cells = [cell.strip() for cell in re.split(r"[|│]", stripped.strip("|│"))]
                candidate = cells[0] if cells else ""
            else:
                candidate = stripped.split(maxsplit=1)[0] if stripped else ""
            if not member_pattern.fullmatch(candidate) or candidate.upper() == "NAME":
                continue
            members[candidate] = status_match.group(1).upper()
        return members

    @staticmethod
    def _overall_status(
        checks: list[ServiceCheck],
    ) -> Literal["healthy", "partial", "unhealthy"]:
        statuses = {check.status for check in checks}
        if "unhealthy" in statuses:
            return "unhealthy"
        if "partial" in statuses:
            return "partial"
        return "healthy"

    @staticmethod
    def _compact(text: str, max_chars: int = 500) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= max_chars:
            return compact
        return compact[: max_chars - 3].rstrip() + "..."
