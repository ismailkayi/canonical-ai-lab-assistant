"""Evidence-based postcondition verification for deployed MicroCloud labs."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

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

    def workspace_resource_count(self, workspace: str) -> int | None:
        """Return managed resource count, treating a workspace with no state as empty."""
        env = os.environ.copy()
        env["TF_WORKSPACE"] = workspace
        try:
            result = subprocess.run(
                ["tofu", "state", "list"],
                cwd=self.terraform_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

        output = "\n".join((result.stdout, result.stderr))
        if result.returncode == 0:
            return len([line for line in result.stdout.splitlines() if line.strip()])
        if "No state file was found" in output:
            return 0
        return None

    @staticmethod
    def lxd_project_info(project: str) -> dict[str, Any] | None:
        """Return one LXD project, distinguishing absence from query failure."""
        try:
            result = subprocess.run(
                ["lxc", "query", f"/1.0/projects/{quote(project, safe='')}"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not inspect LXD project '{project}': {exc}") from exc

        if result.returncode != 0:
            output = "\n".join((result.stdout, result.stderr)).lower()
            if "not found" in output or 'status":404' in output:
                return None
            raise RuntimeError(
                f"Could not inspect LXD project '{project}': " f"{ClusterVerifier._compact(output)}"
            )

        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"LXD returned invalid project data for '{project}'") from exc
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if not isinstance(metadata, dict):
            raise RuntimeError(f"LXD returned no project metadata for '{project}'")
        return metadata

    @staticmethod
    def lxd_network_info(network: str) -> dict[str, Any] | None:
        """Return one global managed network from the default LXD project."""
        try:
            result = subprocess.run(
                [
                    "lxc",
                    "query",
                    f"/1.0/networks/{quote(network, safe='')}?project=default",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not inspect LXD network '{network}': {exc}") from exc

        if result.returncode != 0:
            output = "\n".join((result.stdout, result.stderr)).lower()
            if "not found" in output or 'status":404' in output:
                return None
            raise RuntimeError(
                f"Could not inspect LXD network '{network}': " f"{ClusterVerifier._compact(output)}"
            )

        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"LXD returned invalid network data for '{network}'") from exc
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if not isinstance(metadata, dict):
            raise RuntimeError(f"LXD returned no network metadata for '{network}'")
        return metadata

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
        lxd_project = str(spec.get("lxd_project_name", ""))
        if not lxd_project:
            return VerificationReport(
                workspace=workspace,
                status="unhealthy",
                expected_nodes=expected_nodes,
                expected_osds=expected_osds,
                checks=(
                    ServiceCheck(
                        name="lxd-project",
                        status="unhealthy",
                        command_ok=False,
                        detail="deployment_spec is missing lxd_project_name",
                    ),
                ),
            )
        expected_names = {f"{prefix}-node-{index}" for index in range(1, expected_nodes + 1)}

        checks: list[ServiceCheck] = []
        commands = {
            "microcloud": ["microcloud", "cluster", "list"],
            "lxd": ["lxc", "cluster", "list"],
            "microceph": ["microceph", "cluster", "list"],
            "microovn": ["microovn", "cluster", "list"],
        }
        for name, command in commands.items():
            returncode, output = self._run_in_node(lxd_project, initiator, command)
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

        checks.append(self._check_ceph_health(lxd_project, initiator, expected_osds))
        if spec.get("network_mode") == "fully-segregated-4nic":
            checks.append(
                self._check_segregated_networks(
                    prefix,
                    lxd_project,
                    expected_nodes,
                    str(spec.get("ovn_underlay_cidr", "")),
                    str(spec.get("ceph_network_cidr", "")),
                )
            )
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
            "lxd_project_name": str(spec.get("lxd_project_name", "")),
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
    def _run_in_node(project: str, node: str, command: list[str]) -> tuple[int, str]:
        try:
            result = subprocess.run(
                ["lxc", "--project", project, "exec", node, "--", *command],
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
        project: str,
        initiator: str,
        expected_osds: int | None,
    ) -> ServiceCheck:
        returncode, output = self._run_in_node(
            project,
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

    def _check_segregated_networks(
        self,
        prefix: str,
        project: str,
        expected_nodes: int,
        ovn_cidr: str,
        ceph_cidr: str,
    ) -> ServiceCheck:
        """Verify addresses and ring connectivity on both dedicated planes."""
        try:
            ovn_network = ipaddress.ip_network(ovn_cidr, strict=True)
            ceph_network = ipaddress.ip_network(ceph_cidr, strict=True)
        except ValueError as exc:
            return ServiceCheck(
                name="segregated-networks",
                status="unhealthy",
                command_ok=False,
                expected_members=expected_nodes or None,
                observed_members=0,
                detail=f"Invalid persisted network CIDR: {exc}",
            )
        if (
            expected_nodes <= 0
            or expected_nodes + 9 >= ovn_network.num_addresses - 1
            or expected_nodes + 9 >= ceph_network.num_addresses - 1
        ):
            return ServiceCheck(
                name="segregated-networks",
                status="unhealthy",
                command_ok=False,
                expected_members=expected_nodes or None,
                observed_members=0,
                detail="Persisted network geometry has insufficient node addresses.",
            )

        failures: list[str] = []
        observed = 0
        for index in range(1, expected_nodes + 1):
            node = f"{prefix}-node-{index}"
            ovn_ip = str(ovn_network[index + 9])
            ceph_ip = str(ceph_network[index + 9])
            peer_index = index % expected_nodes + 1
            peer_ovn_ip = str(ovn_network[peer_index + 9])
            peer_ceph_ip = str(ceph_network[peer_index + 9])
            command = [
                "bash",
                "-lc",
                (
                    "set -euo pipefail; "
                    "for iface in mgmt0 ovn-uplink ovn-underlay ceph-general; do "
                    'ip link show dev "$iface" >/dev/null; done; '
                    "! ip -o address show dev ovn-uplink | grep -Eq 'inet6? '; "
                    f"ip -4 -o address show dev ovn-underlay | grep -Fq ' {ovn_ip}/'; "
                    f"ip -4 -o address show dev ceph-general | grep -Fq ' {ceph_ip}/'; "
                    "ip -4 route show default | grep -q ' dev mgmt0'; "
                    f"ping -I ovn-underlay -c 1 -W 2 {peer_ovn_ip} >/dev/null; "
                    f"ping -I ceph-general -c 1 -W 2 {peer_ceph_ip} >/dev/null"
                ),
            ]
            returncode, output = self._run_in_node(project, node, command)
            if returncode == 0:
                observed += 1
            else:
                failures.append(f"{node}: {self._compact(output) or 'plane check failed'}")

        initiator = f"{prefix}-node-1"
        for option in ("cluster_network", "public_network"):
            returncode, config_output = self._run_in_node(
                project,
                initiator,
                ["microceph", "cluster", "config", "get", option],
            )
            pattern = rf"\b{option}\b.*\b{re.escape(ceph_cidr)}\b"
            if returncode != 0 or not re.search(pattern, config_output):
                failures.append(f"Ceph {option} is not {ceph_cidr}")

        status: Literal["healthy", "partial", "unhealthy"]
        if not failures:
            status = "healthy"
            detail = (
                f"All {observed} members have four NICs; OVN={ovn_cidr}, "
                f"Ceph public/internal={ceph_cidr}."
            )
        elif observed == 0:
            status = "unhealthy"
            detail = "; ".join(failures)
        else:
            status = "partial"
            detail = "; ".join(failures)

        return ServiceCheck(
            name="segregated-networks",
            status=status,
            command_ok=not failures,
            expected_members=expected_nodes or None,
            observed_members=observed,
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
