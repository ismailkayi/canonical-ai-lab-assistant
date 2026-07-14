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
