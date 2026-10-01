import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_fresh_deploy_refuses_existing_workspace() -> None:
    script = (REPO_ROOT / "scripts" / "deploy_microcloud.sh").read_text()

    assert "already contains managed resources" in script
    assert "Refusing a fresh deploy" in script
    assert "Removing empty stale workspace" in script


def test_storage_roles_use_exact_lxd_serials_without_disk_order_fallback() -> None:
    playbook = (REPO_ROOT / "playbooks" / "microcloud.yml").read_text()
    add_script = (REPO_ROOT / "scripts" / "add_cluster_node.sh").read_text()

    for content in (playbook, add_script):
        assert "lxd_ceph--disk--" in content
        assert "lxd_local--disk" in content
        assert "lxd_ceph-disk-" not in content
        assert "last_disk" not in content


def test_lifecycle_scripts_bind_state_and_apply_saved_plans() -> None:
    add_script = (REPO_ROOT / "scripts" / "add_cluster_node.sh").read_text()
    scale_script = (REPO_ROOT / "scripts" / "scale_microcloud.sh").read_text()
    cleanup_script = (REPO_ROOT / "scripts" / "cleanup_microcloud.sh").read_text()

    for content in (add_script, scale_script, cleanup_script):
        assert "expected-state-lineage" in content
        assert "expected-state-serial" in content
        assert "expected-current-nodes" in content
        assert "expected-target-nodes" in content

    deploy_script = (REPO_ROOT / "scripts" / "deploy_microcloud.sh").read_text()
    for content in (deploy_script, add_script, cleanup_script):
        assert "canonical-ai-lab-assistant-terraform-${UID}.lock" in content
        assert "flock -x 9" in content
        assert "LAB_AI_TERRAFORM_LOCK_FD" in content

    assert "tofu plan -input=false" in add_script
    assert 'tofu apply -auto-approve -parallelism=1 "${PLAN_FILE}"' in add_script
    assert "tofu plan -destroy" in cleanup_script
    assert 'tofu apply -auto-approve "${PLAN_FILE}"' in cleanup_script
    assert '.get(sys.argv[1], "")' in cleanup_script
    assert '[[ -n "${SPEC_SSH_PUBLIC_KEY}" ]]' in cleanup_script
    assert "SPEC_SSH_PUBLIC_KEY" in add_script


def test_shell_health_fallback_is_fail_closed() -> None:
    script = (REPO_ROOT / "scripts" / "verify_cluster_health.sh").read_text()

    assert "(command failed)" in script
    assert "HEALTH_WARN" in script
    assert 'elif echo "${CEPH_STATUS}"' in script
    assert "OVERALL_OK=false" in script


def test_fully_segregated_network_contract_is_wired_end_to_end() -> None:
    terraform = (REPO_ROOT / "terraform" / "main.tf").read_text()
    playbook = (REPO_ROOT / "playbooks" / "microcloud.yml").read_text()
    deploy = (REPO_ROOT / "scripts" / "deploy_microcloud.sh").read_text()
    add_node = (REPO_ROOT / "scripts" / "add_cluster_node.sh").read_text()
    health = (REPO_ROOT / "scripts" / "verify_cluster_health.sh").read_text()

    assert 'default     = "standard-2nic"' in terraform
    assert 'name = "eth2"' in terraform
    assert 'name = "eth3"' in terraform
    assert '"cloud-init.network-config"' in terraform
    assert '"user.canonical-ai-lab-assistant.cidr"' in terraform
    assert "network_mode        = var.microcloud_network_mode" in terraform

    assert "ovn_underlay_ip:" in playbook
    assert "public_network: {{ microcloud_ceph_network_cidr }}" in playbook
    assert "internal_network: {{ microcloud_ceph_network_cidr }}" in playbook
    assert "lxd_ceph--disk--" in playbook

    for content in (deploy, add_node):
        assert "microcloud_network_mode" in content
        assert "microcloud_ovn_underlay_cidr" in content
        assert "microcloud_ceph_network_cidr" in content

    assert "ovn-underlay" in health
    assert "ceph-general" in health
    assert "cluster_network" in health
    assert "public_network" in health


def test_fresh_deploy_preflights_every_default_project_resource_name() -> None:
    terraform = (REPO_ROOT / "terraform" / "main.tf").read_text()
    deploy = (REPO_ROOT / "scripts" / "deploy_microcloud.sh").read_text()
    add_node = (REPO_ROOT / "scripts" / "add_cluster_node.sh").read_text()
    cleanup = (REPO_ROOT / "scripts" / "cleanup_microcloud.sh").read_text()

    assert 'name = "ca-${var.resource_namespace}-up"' in terraform
    assert 'name  = "ca-${var.resource_namespace}-ov"' in terraform
    assert 'name  = "ca-${var.resource_namespace}-ce"' in terraform
    assert "user.canonical-ai-lab-assistant.owner" in terraform
    assert "resource_namespace  = var.resource_namespace" in terraform

    for resource_type in ("INSTANCES", "PROFILES", "NETWORKS", "VOLUMES"):
        assert f"EXISTING_{resource_type}" in deploy
        assert f"{resource_type[:-1].lower()}:" in deploy
    assert "Could not inspect the complete default-project LXD namespace" in deploy
    assert deploy.count("assert_lxd_names_available") >= 3
    assert deploy.index('assert_lxd_names_available\n\nif [[ "${AUTO_APPROVE}"') < deploy.index(
        'read -r -p "Proceed with deployment?'
    )
    assert deploy.rindex("assert_lxd_names_available") < deploy.index(
        'tofu workspace new "${WORKSPACE_NAME}"'
    )
    assert "reconciling exact owned names" in deploy
    assert "tofu import" in deploy
    assert "ownership/role mismatch" in deploy
    assert "expected bridge, found" in deploy
    assert "Automatic recovery is limited to ownership-checked networks" in deploy
    assert "reconciling with one retry" not in deploy
    assert "SPEC_RESOURCE_NAMESPACE" in add_node
    assert "resource_namespace" in cleanup
    assert "using a destroy-only fallback" in cleanup


@pytest.mark.parametrize("succeeds", [True, False])
def test_shell_auto_sizing_uses_shared_helper_and_preserves_supplied_fields(
    tmp_path, succeeds
) -> None:
    script = (REPO_ROOT / "scripts" / "deploy_microcloud.sh").read_text()
    start = script.index("auto_size_nodes() {")
    end = script.index(
        "\n# -----------------------------------------------------------------------", start
    )
    auto_size_function = script[start:end]
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$@" > "$CAPTURE_FILE"\n'
        + ("printf '6 16384 60 100\\n'\n" if succeeds else "exit 1\n")
    )
    python.chmod(0o755)
    captured_args = tmp_path / "arguments"
    environment = {
        **os.environ,
        "REPO_ROOT": str(tmp_path),
        "CAPTURE_FILE": str(captured_args),
        "NODES": "3",
        "NODE_CPU": "6",
        "NODE_MEMORY_MB": "",
        "ROOT_DISK_GIB": "",
        "CEPH_DISK_GIB": "",
        "CEPH_DISKS_PER_NODE": "2",
        "LOCAL_DISK_GIB": "10",
        "SIZING_TIER": "medium",
        "WORKLOAD_DESCRIPTION": "storage benchmark training",
        "DATASET_SIZE_GIB": "100",
        "TF_VAR_lxd_storage_pool": "fastpool",
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -eu\n"
            "log_error() { printf '%s\\n' \"$1\" >&2; }\n"
            + auto_size_function
            + "\nauto_size_nodes\n"
            'printf \'%s %s %s %s\\n\' "$NODE_CPU" "$NODE_MEMORY_MB" "$ROOT_DISK_GIB" "$CEPH_DISK_GIB"\n',
        ],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    arguments = captured_args.read_text().splitlines()
    assert arguments[:3] == ["-m", "lab_ai_assistant.cli", "resolve-sizing"]
    assert "--node-cpu=6" in arguments
    assert "--ceph-disks-per-node=2" in arguments
    assert "--local-disk-gib=10" in arguments
    assert "--dataset-size-gib=100" in arguments
    assert "--workload-description=storage benchmark training" in arguments
    assert "--storage-pool=fastpool" in arguments
    assert "usable_disk=120" not in script
    if succeeds:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "6 16384 60 100"
    else:
        assert result.returncode == 1
        assert "Could not resolve safe sizing" in result.stderr


@pytest.mark.parametrize("pool_exists", [True, False])
def test_shell_deploy_cannot_switch_away_from_approved_pool(tmp_path, pool_exists) -> None:
    script = (REPO_ROOT / "scripts" / "deploy_microcloud.sh").read_text()
    start = script.index("detect_lxd_defaults() {")
    end = script.index("\nvalidate_plane_subnet_availability()", start)
    detect_function = script[start:end]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    lxc = bin_dir / "lxc"
    lxc.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        "  'network list') printf 'lxdbr0\\n';;\n"
        "  'network show') printf 'type: bridge\\n';;\n"
        "  'network get') printf '10.1.1.1/24\\n';;\n"
        f"  'storage show') exit {0 if pool_exists else 1};;\n"
        "esac\n"
    )
    lxc.chmod(0o755)
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -eu\n"
            "log_error() { printf '%s\\n' \"$1\" >&2; }\n"
            "print_kv() { :; }\n"
            + detect_function
            + "\ndetect_lxd_defaults\nprintf '%s\\n' \"$TF_VAR_lxd_storage_pool\"\n",
        ],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "STORAGE_POOL": "approved-pool",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert "--storage-pool=*)" in script
    if pool_exists:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "approved-pool"
    else:
        assert result.returncode == 1
        assert "refusing to select another pool" in result.stderr
