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
