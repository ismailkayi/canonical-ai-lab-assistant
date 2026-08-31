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
    assert "LXD project ownership does not match" in cleanup_script
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
    assert "user.canonical-ai-lab-assistant.cidr" in deploy
    assert "network_mode         = var.microcloud_network_mode" in terraform

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


def test_project_resources_are_isolated_and_global_networks_are_owned() -> None:
    terraform = (REPO_ROOT / "terraform" / "main.tf").read_text()
    deploy = (REPO_ROOT / "scripts" / "deploy_microcloud.sh").read_text()
    add_node = (REPO_ROOT / "scripts" / "add_cluster_node.sh").read_text()
    cleanup = (REPO_ROOT / "scripts" / "cleanup_microcloud.sh").read_text()
    health = (REPO_ROOT / "scripts" / "verify_cluster_health.sh").read_text()
    listing = (REPO_ROOT / "scripts" / "list_microcloud_environments.sh").read_text()

    assert 'resource "lxd_project" "environment"' in terraform
    assert '"features.networks"' in terraform
    assert '"features.storage.volumes"' in terraform
    assert "ansible_lxd_project" in terraform
    assert "version              = 3" in terraform
    assert "lxd_project_name" in terraform
    assert "lxd_network_name" not in terraform

    for resource in (
        'resource "lxd_volume" "microcloud_ceph_disks"',
        'resource "lxd_instance" "microcloud_nodes"',
    ):
        block = terraform.split(resource, 1)[1].split("\n}", 1)[0]
        assert "project" in block

    # Managed bridge networks are the only global resources because LXD rejects
    # bridge-type networks in non-default projects. They are hash-named and
    # tagged with their owner project for preflight and orphan reporting.
    assert '"features.networks"                          = "false"' in terraform
    assert '"features.profiles"                          = "false"' in terraform
    assert "user.canonical-ai-lab-assistant.project" in deploy

    assert "Automatic retry is disabled" in deploy
    assert "--project" in add_node
    assert "LXD project ownership does not match the saved deployment" in add_node
    assert "validate_owned_network" in add_node
    assert "ownership/role does not match the saved deployment" in add_node
    assert "--project" in health
    assert "--project" in listing
    assert "LXD project ownership does not match" in cleanup


def test_orphan_audit_is_read_only_and_owner_scoped() -> None:
    script = (REPO_ROOT / "scripts" / "list_orphaned_lxd_projects.sh").read_text()

    assert "managed-by" in script
    assert "lxd_project.environment" in script
    assert "ORPHAN" in script
    assert "project delete" not in script
    assert "\nrm " not in script


def test_orphan_cleanup_is_owner_scoped_and_refuses_managed_projects() -> None:
    script = (REPO_ROOT / "scripts" / "cleanup_orphaned_lxd_project.sh").read_text()

    assert "project ownership mismatch" in script
    assert "project is referenced by Terraform workspace" in script
    assert "network_owner" in script
    assert "network '${network}' owner mismatch" in script
    assert 'lxc project delete --force "${PROJECT}"' in script
    assert 'lxc --project default network delete "${network}"' in script
    assert "no owned project or tagged networks were found" in script
    assert "Terraform workspaces cannot be enumerated" in script
    assert "Terraform state for '${candidate}' is unreadable" in script
    assert "project state in '${candidate}' is unreadable" in script
    assert "ownership changed before deletion" in script
    assert "tagged network set changed after inspection" in script


def test_global_network_operations_pin_the_default_project() -> None:
    deploy = (REPO_ROOT / "scripts" / "deploy_microcloud.sh").read_text()
    cleanup = (REPO_ROOT / "scripts" / "cleanup_microcloud.sh").read_text()
    orphan_cleanup = (REPO_ROOT / "scripts" / "cleanup_orphaned_lxd_project.sh").read_text()

    assert 'lxc --project default network create "${name}"' in deploy
    assert 'lxc --project default network show "${network_name}"' in deploy
    assert 'lxc --project default network delete "${created}"' in deploy
    assert 'lxc --project default network delete "${network}"' in cleanup
    assert 'lxc --project default network delete "${network}"' in orphan_cleanup
    assert "rolling back newly created networks" in deploy
    assert "ownership changed during cleanup" in cleanup
    assert "Could not enumerate host IPv4 routes" in deploy
    assert "Could not enumerate global LXD networks" in deploy


def test_legacy_volume_audit_pins_the_default_project() -> None:
    audit = (REPO_ROOT / "scripts" / "list_orphaned_lxd_projects.sh").read_text()

    assert "lxc --project default storage volume get" in audit
    assert "lxc --project default storage volume list" in audit
