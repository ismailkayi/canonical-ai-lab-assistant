from lab_ai_assistant.sizing import SizingAdvisor


def test_residual_sizing_does_not_manufacture_capacity() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {
            "cpu_cores": 0,
            "ram_total_mb": 0,
            "storage_available_gib": 480,
        },
        nodes=3,
        residual_capacity=True,
    )

    assert sizing.host_cpu == 0
    assert sizing.host_ram_gb == 0
    assert not sizing.fits_host()


def test_fit_includes_root_and_ceph_storage() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {
            "cpu_cores": 16,
            "ram_total_mb": 32 * 1024,
            "storage_available_gib": 350,
        },
        nodes=3,
        residual_capacity=True,
    )

    assert sizing.total_storage_gb() == (sizing.root_disk_gb + sizing.ceph_disk_gb) * sizing.nodes
    assert sizing.fits_host() == (sizing.total_storage_gb() <= 350)


def test_host_aware_sizing_accounts_for_multiple_ceph_and_local_disks() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {
            "cpu_cores": 26,
            "ram_total_mb": 34 * 1024,
            "storage_available_gib": 782,
        },
        nodes=3,
        residual_capacity=True,
        ceph_disks_per_node=2,
        local_disk_gib=20,
    )

    expected = (
        sizing.root_disk_gb + 2 * sizing.ceph_disk_gb + sizing.local_disk_gib
    ) * sizing.nodes
    assert sizing.total_ceph_gb() == sizing.ceph_disk_gb * 2 * 3
    assert sizing.total_storage_gb() == expected
    assert sizing.total_storage_gb() <= 782
    assert sizing.fits_host()
