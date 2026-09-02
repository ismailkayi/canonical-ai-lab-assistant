from pathlib import Path

from typer.testing import CliRunner

from lab_ai_assistant.cli import app
from lab_ai_assistant.config import Config

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
    (assets_dir / "main.tf").write_text('terraform { required_version = ">= 1.12" }\n')

    refreshed = Config(state_dir=state_dir)

    assert ">= 1.12" in (refreshed.terraform_dir / "main.tf").read_text()
    assert state_marker.read_text() == "prototype"


def test_source_config_keeps_terraform_in_checkout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SNAP", raising=False)

    config = Config(repo_root=REPO_ROOT, state_dir=tmp_path)

    assert config.terraform_assets_dir == REPO_ROOT / "terraform"
    assert config.terraform_dir == REPO_ROOT / "terraform"


def test_snap_bootstrap_requires_explicit_host_setup(monkeypatch) -> None:
    monkeypatch.setenv("SNAP", "/snap/lab-ai/current")
    runner = CliRunner()

    result = runner.invoke(app, ["bootstrap"])

    assert result.exit_code == 0
    assert "does not change the host by default" in result.stdout
    assert "bootstrap --host-setup" in result.stdout


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
    assert "source: terraform" in manifest
