from pathlib import Path

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
