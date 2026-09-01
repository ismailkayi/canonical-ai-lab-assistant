from lab_ai_assistant.tools import get_tool_by_name, validate_tool_parameters


def test_lifecycle_tools_expose_only_supported_geometry() -> None:
    scale = get_tool_by_name("scale_environment")
    add = get_tool_by_name("add_cluster_node")

    assert set(scale["parameters"]["properties"]) == {"workspace", "target_nodes"}
    assert set(add["parameters"]["properties"]) == {"workspace", "add_nodes"}


def test_tool_validation_rejects_ranges_unknown_fields_and_bad_names() -> None:
    assert not validate_tool_parameters(
        "add_cluster_node", {"workspace": "lab_microcloud", "add_nodes": 0}
    )[0]
    assert not validate_tool_parameters(
        "deploy_microcloud", {"nodes": 3, "network_interface": "eth0"}
    )[0]
    assert not validate_tool_parameters("delete_environment", {"workspace": "lab;destroy"})[0]
    assert validate_tool_parameters(
        "deploy_microcloud",
        {
            "nodes": 3,
            "node_cpu": 4,
            "node_memory_mb": 8192,
            "root_disk_gib": 40,
            "ceph_disk_gib": 50,
            "ceph_disks_per_node": 2,
            "local_disk_gib": 0,
        },
    )[0]


def test_deploy_tool_exposes_optional_fully_segregated_networking() -> None:
    deploy = get_tool_by_name("deploy_microcloud")
    properties = deploy["parameters"]["properties"]

    assert properties["network_mode"]["enum"] == [
        "standard-2nic",
        "fully-segregated-4nic",
    ]
    assert validate_tool_parameters(
        "deploy_microcloud",
        {
            "nodes": 3,
            "network_mode": "fully-segregated-4nic",
            "ovn_underlay_cidr": "172.28.42.0/24",
            "ceph_network_cidr": "172.29.42.0/24",
        },
    )[0]
    assert not validate_tool_parameters(
        "deploy_microcloud",
        {"nodes": 3, "network_mode": "four-nics"},
    )[0]


def test_custom_sizing_requires_all_explicit_resource_values() -> None:
    assert validate_tool_parameters(
        "deploy_microcloud",
        {
            "nodes": 3,
            "sizing_tier": "custom",
            "node_cpu": 2,
            "node_memory_mb": 4096,
            "root_disk_gib": 30,
            "ceph_disk_gib": 20,
        },
    )[0]
    assert not validate_tool_parameters(
        "deploy_microcloud",
        {"nodes": 3, "sizing_tier": "custom", "node_cpu": 2},
    )[0]
