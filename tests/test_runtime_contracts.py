from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_fresh_deploy_refuses_existing_workspace() -> None:
    script = (REPO_ROOT / "scripts" / "deploy_microcloud.sh").read_text()

    assert "Workspace '${WORKSPACE_NAME}' already exists" in script
    assert "Refusing a fresh deploy" in script


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
    assert "SPEC_SSH_PUBLIC_KEY" in add_script


def test_shell_health_fallback_is_fail_closed() -> None:
    script = (REPO_ROOT / "scripts" / "verify_cluster_health.sh").read_text()

    assert "(command failed)" in script
    assert "HEALTH_WARN" in script
    assert 'elif echo "${CEPH_STATUS}"' in script
    assert "OVERALL_OK=false" in script
