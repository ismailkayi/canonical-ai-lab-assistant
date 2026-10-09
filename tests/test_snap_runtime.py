import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lab_ai_assistant.cli import app
from lab_ai_assistant.config import Config, get_snap_root
from lab_ai_assistant.orchestrator import LabOrchestrator

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_snap_config_copies_assets_and_preserves_working_state(tmp_path: Path, monkeypatch) -> None:
    snap_root = tmp_path / "snap"
    assets_dir = snap_root / "terraform"
    (snap_root / "scripts").mkdir(parents=True)
    assets_dir.mkdir()
    (assets_dir / "main.tf").write_text('terraform { required_version = ">= 1.9" }\n')
    (assets_dir / ".terraform.lock.hcl").write_text("# locked providers\n")
    state_dir = tmp_path / "common"

    monkeypatch.setenv("SNAP", str(snap_root))
    config = Config(state_dir=state_dir)

    assert config.repo_root == snap_root
    assert config.terraform_assets_dir == assets_dir
    assert config.terraform_dir == state_dir / "terraform"
    assert (config.terraform_dir / "main.tf").read_text() == (assets_dir / "main.tf").read_text()
    assert (config.terraform_dir / ".terraform.lock.hcl").is_file()

    state_marker = config.terraform_dir / ".terraform" / "environment"
    state_marker.parent.mkdir()
    state_marker.write_text("prototype")
    workspace_state = (
        config.terraform_dir / "terraform.tfstate.d" / "prototype" / "terraform.tfstate"
    )
    workspace_state.parent.mkdir(parents=True)
    workspace_state.write_text('{"lineage":"existing-lab","serial":7}\n')
    inventory = state_dir / "inventory_prototype.yaml"
    inventory.write_text("all: {}\n")
    (assets_dir / "main.tf").write_text('terraform { required_version = ">= 1.12" }\n')

    refreshed = Config(state_dir=state_dir)

    assert ">= 1.12" in (refreshed.terraform_dir / "main.tf").read_text()
    assert state_marker.read_text() == "prototype"
    assert workspace_state.read_text() == '{"lineage":"existing-lab","serial":7}\n'
    assert inventory.read_text() == "all: {}\n"


def test_source_config_keeps_terraform_in_checkout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SNAP", raising=False)

    config = Config(repo_root=REPO_ROOT, state_dir=tmp_path)

    assert config.terraform_assets_dir == REPO_ROOT / "terraform"
    assert config.terraform_dir == REPO_ROOT / "terraform"


def test_snap_bootstrap_requires_explicit_host_setup(monkeypatch) -> None:
    monkeypatch.setattr("lab_ai_assistant.cli.get_snap_root", lambda: Path("/snap/lab-ai/current"))
    runner = CliRunner()

    result = runner.invoke(app, ["bootstrap"])

    assert result.exit_code == 0
    assert "does not change the host by default" in result.stdout
    assert "bootstrap --host-setup" in result.stdout


def test_unrelated_editor_snap_does_not_change_source_runtime(tmp_path, monkeypatch) -> None:
    editor_root = tmp_path / "code"
    editor_root.mkdir()
    editor_state = tmp_path / "editor-common"
    monkeypatch.setenv("SNAP", str(editor_root))
    monkeypatch.setenv("SNAP_USER_COMMON", str(editor_state))
    monkeypatch.setenv("HOME", str(tmp_path))

    config = Config(repo_root=REPO_ROOT)
    orchestrator = LabOrchestrator(config)

    assert get_snap_root() is None
    assert config.state_dir == tmp_path / ".canonical-ai-lab-assistant"
    assert config.terraform_dir == REPO_ROOT / "terraform"
    assert "SNAP" not in orchestrator._script_environment()
    assert "SNAP_USER_COMMON" not in orchestrator._script_environment()
    assert not editor_state.exists()


@pytest.mark.parametrize("succeeds", [True, False])
def test_snap_shell_sizing_uses_bundled_python_and_modules(tmp_path, succeeds) -> None:
    script = (REPO_ROOT / "scripts" / "deploy_microcloud.sh").read_text()
    start = script.index("auto_size_nodes() {")
    end = script.index(
        "\n# -----------------------------------------------------------------------", start
    )
    snap_root = tmp_path / "package"
    python = snap_root / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$PYTHONPATH" > "$CAPTURE_PATH"\n'
        'printf \'%s\\n\' "$@" > "$CAPTURE_ARGS"\n'
        + ("printf '4 8192 40 25\\n'\n" if succeeds else "exit 1\n")
    )
    python.chmod(0o755)
    capture_path = tmp_path / "module-path"
    capture_args = tmp_path / "arguments"
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -eu\nlog_error() { printf '%s\\n' \"$1\" >&2; }\n"
            + script[start:end]
            + "\nauto_size_nodes\n"
            'printf \'%s %s %s %s\\n\' "$NODE_CPU" "$NODE_MEMORY_MB" "$ROOT_DISK_GIB" "$CEPH_DISK_GIB"\n',
        ],
        env={
            **os.environ,
            "SNAP": str(snap_root),
            "REPO_ROOT": str(snap_root),
            "CAPTURE_PATH": str(capture_path),
            "CAPTURE_ARGS": str(capture_args),
            "PYTHONPATH": "/unrelated/modules",
            "NODES": "3",
            "NODE_CPU": "4",
            "NODE_MEMORY_MB": "",
            "ROOT_DISK_GIB": "",
            "CEPH_DISK_GIB": "",
            "CEPH_DISKS_PER_NODE": "2",
            "LOCAL_DISK_GIB": "0",
            "SIZING_TIER": "small",
            "WORKLOAD_DESCRIPTION": "training",
            "DATASET_SIZE_GIB": "100",
            "TF_VAR_lxd_storage_pool": "approved-pool",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert capture_path.read_text().strip() == (
        f"{snap_root}/lib/python3.12/site-packages:{snap_root}/usr/lib/python3/dist-packages"
    )
    arguments = capture_args.read_text().splitlines()
    assert arguments[:3] == ["-m", "lab_ai_assistant.cli", "resolve-sizing"]
    assert "--node-cpu=4" in arguments
    assert "--dataset-size-gib=100" in arguments
    assert "--storage-pool=approved-pool" in arguments
    if succeeds:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "4 8192 40 25"
    else:
        assert result.returncode == 1
        assert "Could not resolve safe sizing" in result.stderr


def test_lifecycle_scripts_accept_the_writable_terraform_directory() -> None:
    script_names = (
        "add_cluster_node.sh",
        "cleanup_microcloud.sh",
        "deploy_microcloud.sh",
        "list_microcloud_environments.sh",
        "prep_host.sh",
        "scale_microcloud.sh",
        "verify_cluster_health.sh",
    )

    for script_name in script_names:
        script = (REPO_ROOT / "scripts" / script_name).read_text()
        assert "LAB_AI_TERRAFORM_DIR" in script

    for script_name in ("add_cluster_node.sh", "deploy_microcloud.sh"):
        script = (REPO_ROOT / "scripts" / script_name).read_text()
        assert 'RUNTIME_DIR="$(dirname "${TERRAFORM_DIR}")"' in script
        assert 'INVENTORY_FILE="${RUNTIME_DIR}/inventory_' in script


def test_snapcraft_bundles_pinned_runtime_and_immutable_assets() -> None:
    manifest = (REPO_ROOT / "snap" / "snapcraft.yaml").read_text()

    assert "confinement: classic" in manifest
    assert "ansible-core==2.20.1" in manifest
    assert "community.general:==12.1.0" in manifest
    assert "tofu_1.12.6_linux_amd64.zip" in manifest
    assert "providers mirror" in manifest
    assert "source: playbooks" in manifest
    assert "source: scripts" in manifest
    assert 'chmod 0755 "$CRAFT_PART_INSTALL/scripts/"*.sh' in manifest
    assert "source: terraform" in manifest
