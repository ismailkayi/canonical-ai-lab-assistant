import json
from pathlib import Path
from types import SimpleNamespace

from lab_ai_assistant.verification import ClusterVerifier


class FakeVerifier(ClusterVerifier):
    def __init__(self, osds: int, offline_member: str | None = None):
        super().__init__(Path("terraform"))
        self.osds = osds
        self.offline_member = offline_member

    def deployment_spec(self, workspace):
        return {
            "node_count": 3,
            "ceph_disks_per_node": 2,
            "lxd_project_name": "cala-lab-microcloud-12345678",
        }

    def _run_in_node(self, project, node, command):
        if command[0] == "microceph.ceph":
            return (
                0,
                "health: HEALTH_OK\n"
                "mon: 3 daemons, quorum lab-microcloud-node-1,"
                "lab-microcloud-node-2,lab-microcloud-node-3\n"
                f"osd: {self.osds} osds: {self.osds} up",
            )
        members = "\n".join(
            f"lab-microcloud-node-{index} "
            f"{'OFFLINE' if self.offline_member == f'lab-microcloud-node-{index}' else 'ONLINE'}"
            for index in range(1, 4)
        )
        return 0, members


def test_verification_detects_missing_osd() -> None:
    assert FakeVerifier(6).verify("lab_microcloud").status == "healthy"

    report = FakeVerifier(5).verify("lab_microcloud")

    assert report.status == "partial"
    assert "observed_osds=5" in report.checks[-1].detail


def test_verification_rejects_listed_but_offline_member() -> None:
    report = FakeVerifier(6, offline_member="lab-microcloud-node-2").verify("lab_microcloud")

    assert report.status == "partial"
    assert any("node-2" in check.detail for check in report.checks[:-1])


def test_member_parser_does_not_confuse_node_two_with_node_twenty() -> None:
    members = ClusterVerifier._parse_member_statuses(
        "| lab-microcloud-node-20 | ONLINE |\n| lab-microcloud-node-2 | OFFLINE |"
    )

    assert members == {
        "lab-microcloud-node-20": "ONLINE",
        "lab-microcloud-node-2": "OFFLINE",
    }


class UnexpectedMemberVerifier(FakeVerifier):
    def _run_in_node(self, project, node, command):
        returncode, output = super()._run_in_node(project, node, command)
        if command[0] != "microceph.ceph":
            output += "\nlab-microcloud-node-20 ONLINE"
        return returncode, output


def test_verification_rejects_unexpected_member() -> None:
    report = UnexpectedMemberVerifier(6).verify("lab_microcloud")

    assert report.status == "partial"
    assert any("unexpected=['lab-microcloud-node-20']" in check.detail for check in report.checks)


class ArbitraryUnexpectedMemberVerifier(FakeVerifier):
    def _run_in_node(self, project, node, command):
        returncode, output = super()._run_in_node(project, node, command)
        if command[0] != "microceph.ceph":
            output += "\n| rogue | ONLINE |"
        return returncode, output


def test_verification_rejects_arbitrary_unexpected_member_name() -> None:
    report = ArbitraryUnexpectedMemberVerifier(6).verify("lab_microcloud")

    assert report.status == "partial"
    assert any("unexpected=['rogue']" in check.detail for check in report.checks)


class SegregatedVerifier(FakeVerifier):
    def __init__(self, broken_node: str | None = None):
        super().__init__(6)
        self.broken_node = broken_node

    def deployment_spec(self, workspace):
        return {
            "node_count": 3,
            "ceph_disks_per_node": 2,
            "network_mode": "fully-segregated-4nic",
            "ovn_underlay_cidr": "172.28.42.0/24",
            "ceph_network_cidr": "172.29.42.0/24",
            "lxd_project_name": "cala-lab-microcloud-12345678",
        }

    def _run_in_node(self, project, node, command):
        if command[:4] == ["microceph", "cluster", "config", "get"]:
            key = command[4]
            return (
                0,
                f"| 0 | {key} | 172.29.42.0/24 |",
            )
        if command[0] == "bash":
            if node == self.broken_node:
                return 1, "ovn-underlay route failed"
            return 0, ""
        return super()._run_in_node(project, node, command)


def test_verification_checks_fully_segregated_network_planes() -> None:
    report = SegregatedVerifier().verify("lab_microcloud")

    check = next(item for item in report.checks if item.name == "segregated-networks")
    assert report.status == "healthy"
    assert check.status == "healthy"
    assert check.observed_members == 3
    assert "Ceph public/internal=172.29.42.0/24" in check.detail


def test_verification_fails_closed_on_a_broken_network_plane() -> None:
    report = SegregatedVerifier(broken_node="lab-microcloud-node-2").verify("lab_microcloud")

    check = next(item for item in report.checks if item.name == "segregated-networks")
    assert report.status == "partial"
    assert check.status == "partial"
    assert check.observed_members == 2
    assert "node-2" in check.detail


def test_lxd_project_info_distinguishes_project_from_absence(monkeypatch) -> None:
    payload = {
        "metadata": {
            "name": "cala-training-12345678",
            "config": {
                "user.canonical-ai-lab-assistant.managed-by": ("canonical-ai-lab-assistant")
            },
        }
    }
    monkeypatch.setattr(
        "lab_ai_assistant.verification.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    assert ClusterVerifier.lxd_project_info("cala-training-12345678") == (payload["metadata"])


def test_lxd_project_info_returns_none_only_for_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        "lab_ai_assistant.verification.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Error: Project not found",
        ),
    )

    assert ClusterVerifier.lxd_project_info("missing") is None


def test_lxd_network_info_reads_default_project_metadata(monkeypatch) -> None:
    payload = {
        "metadata": {
            "name": "ca-12345678-up",
            "config": {
                "user.canonical-ai-lab-assistant.owner": "lab_microcloud",
                "user.canonical-ai-lab-assistant.project": ("cala-lab-microcloud-12345678"),
            },
        }
    }
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("lab_ai_assistant.verification.subprocess.run", fake_run)

    assert ClusterVerifier.lxd_network_info("ca-12345678-up") == payload["metadata"]
    assert captured["command"][-1].endswith("?project=default")
