import pytest

from lab_ai_assistant.scenarios import SizingTier
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
        sizing.root_disk_gb + 2 * sizing.ceph_disk_gb + sizing.topology.local_disk_gib
    ) * sizing.nodes
    assert sizing.total_ceph_gb() == sizing.ceph_disk_gb * 2 * 3
    assert sizing.total_storage_gb() == expected
    assert sizing.total_storage_gb() <= 782
    assert sizing.fits_host()


@pytest.mark.parametrize("profile", ["conservative", "balanced", "medium", "large", "performance"])
@pytest.mark.parametrize("nodes", [3, 5, 10])
@pytest.mark.parametrize("osds", [1, 2, 8])
@pytest.mark.parametrize("local", [0, 10])
def test_automatic_sizing_stays_within_feasible_budgets(profile, nodes, osds, local) -> None:
    for multiplier in (1, 2, 20):
        budget = {
            "cpu_cores": nodes * multiplier,
            "ram_total_mb": nodes * 4096 * multiplier,
            "storage_available_gib": nodes * (20 + 10 * osds + local) * multiplier,
        }
        sizing = SizingAdvisor().host_aware_size(
            budget,
            nodes=nodes,
            profile=profile,
            residual_capacity=True,
            ceph_disks_per_node=osds,
            local_disk_gib=local,
        )

        assert sizing.fits_host()
        assert sizing.total_cpu() <= budget["cpu_cores"]
        assert sizing.topology.total_ram_mb <= budget["ram_total_mb"]
        assert sizing.total_storage_gb() <= budget["storage_available_gib"]


def test_performance_sizing_does_not_overrun_the_reported_storage_budget() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {"cpu_cores": 14, "ram_total_mb": 30 * 1024, "storage_available_gib": 731},
        nodes=3,
        profile="performance",
        residual_capacity=True,
    )

    assert sizing.fits_host()
    assert sizing.total_storage_gb() <= 731
    assert sizing.node_cpu == 4
    assert sizing.node_memory_mb == 10 * 1024


def test_large_cpu_ceiling_is_not_an_automatic_allocation_target() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {"cpu_cores": 96, "ram_total_mb": 128 * 1024, "storage_available_gib": 10000},
        nodes=3,
        residual_capacity=True,
    )

    assert sizing.node_cpu == 4
    assert sizing.node_memory_mb == 8 * 1024
    assert sizing.ceph_disk_gb == 50
    assert sizing.total_cpu() == 12
    assert sizing.total_storage_gb() == 270


def test_automatic_osd_count_does_not_multiply_the_dataset_assumption() -> None:
    advisor = SizingAdvisor()
    budget = {"cpu_cores": 96, "ram_total_mb": 128 * 1024, "storage_available_gib": 10000}
    one = advisor.host_aware_size(budget, 3, "medium", residual_capacity=True)
    two = advisor.host_aware_size(
        budget, 3, "medium", residual_capacity=True, ceph_disks_per_node=2
    )
    large = advisor.host_aware_size(budget, 3, "large", residual_capacity=True)

    assert one.total_ceph_gb() == two.total_ceph_gb() == 300
    assert two.ceph_disk_gb == 50
    assert large.total_ceph_gb() == 300
    assert large.total_storage_gb() == 540


def test_complete_storage_geometry_has_one_source_of_totals() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {"cpu_cores": 96, "ram_total_mb": 64 * 1024, "storage_available_gib": 1000},
        nodes=3,
        profile="medium",
        residual_capacity=True,
        node_cpu=8,
        node_memory_mb=16 * 1024,
        root_disk_gib=60,
        ceph_disk_gib=100,
        ceph_disks_per_node=2,
        local_disk_gib=50,
    )

    assert sizing.total_cpu() == 24
    assert sizing.total_ram_gb() == 48
    assert sizing.total_ceph_gb() == 600
    assert sizing.topology.total_root_gib == 180
    assert sizing.topology.total_local_gib == 150
    assert sizing.total_storage_gb() == 930
    assert sizing.to_dict()["totals"]["storage_gib"] == 930
    assert "Totals: 24 vCPU / 48 GiB RAM / 930 GiB storage" in sizing.summary()


def test_static_reference_storage_includes_root_as_well_as_ceph() -> None:
    reference = SizingAdvisor().recommend("custom", nodes=3, override_tier=SizingTier.MEDIUM)

    assert reference.total_cpu() == 24
    assert reference.total_storage_gb() == 480
    assert reference.to_dict()["totals"]["ceph_gb"] == 300


def test_small_and_medium_are_distinct_but_adapt_to_capacity() -> None:
    advisor = SizingAdvisor()
    budget = {"cpu_cores": 96, "ram_total_mb": 64 * 1024, "storage_available_gib": 1000}
    small = advisor.host_aware_size(budget, 3, "small", residual_capacity=True)
    medium = advisor.host_aware_size(budget, 3, "medium", residual_capacity=True)
    constrained = advisor.host_aware_size(
        {**budget, "cpu_cores": 18, "ram_total_mb": 30 * 1024},
        3,
        "medium",
        residual_capacity=True,
    )

    assert small.node_cpu == 4
    assert medium.node_cpu == 8
    assert constrained.node_cpu == 6
    assert constrained.node_memory_mb == 10 * 1024
    assert constrained.fits_host()


def test_explicit_resources_are_not_silently_downsized() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {"cpu_cores": 20, "ram_total_mb": 30 * 1024, "storage_available_gib": 731},
        nodes=3,
        residual_capacity=True,
        node_cpu=8,
        node_memory_mb=16 * 1024,
        root_disk_gib=60,
        ceph_disk_gib=100,
        ceph_disks_per_node=2,
    )

    assert sizing.total_cpu() == 24
    assert sizing.total_ram_gb() == 48
    assert sizing.total_storage_gb() == 780
    assert not sizing.fits_host()


def test_automatic_root_accounts_for_explicit_multiple_osds() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {"cpu_cores": 96, "ram_total_mb": 64 * 1024, "storage_available_gib": 731},
        nodes=3,
        profile="medium",
        residual_capacity=True,
        ceph_disk_gib=100,
        ceph_disks_per_node=2,
    )

    assert sizing.ceph_disk_gb == 100
    assert sizing.root_disk_gb == 43
    assert sizing.total_storage_gb() == 729
    assert sizing.fits_host()


def test_memory_fit_uses_exact_mib_without_rounding_capacity_up() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {"cpu_cores": 96, "ram_total_mb": 4607, "storage_available_gib": 1000},
        nodes=3,
        residual_capacity=True,
        node_memory_mb=1536,
    )

    assert sizing.topology.total_ram_mb == 4608
    assert sizing.total_ram_gb() == 4.5
    assert not sizing.fits_host()
    assert "1.5 GiB RAM (1536 MiB)" in sizing.summary()


def test_dataset_target_includes_replication_and_free_space_headroom() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {"cpu_cores": 96, "ram_total_mb": 64 * 1024, "storage_available_gib": 731},
        nodes=3,
        residual_capacity=True,
        ceph_disks_per_node=2,
        dataset_size_gib=100,
    )

    assert sizing.ceph_disk_gb == 63
    assert sizing.total_ceph_gb() == 378
    assert sizing.topology.ceph_dataset_budget_gib == 100
    assert sizing.fits_host()


def test_impossible_dataset_target_is_not_silently_reduced() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {"cpu_cores": 96, "ram_total_mb": 64 * 1024, "storage_available_gib": 731},
        nodes=3,
        residual_capacity=True,
        dataset_size_gib=1000,
    )

    assert sizing.topology.ceph_dataset_budget_gib >= 1000
    assert not sizing.fits_host()


def test_explicit_ceph_disks_cannot_claim_an_unmet_dataset_target() -> None:
    with pytest.raises(ValueError, match="below the requested 100 GiB"):
        SizingAdvisor().host_aware_size(
            {"cpu_cores": 96, "ram_total_mb": 64 * 1024, "storage_available_gib": 731},
            nodes=3,
            residual_capacity=True,
            ceph_disk_gib=50,
            dataset_size_gib=100,
        )


def test_raw_host_sizing_never_manufactures_resources() -> None:
    sizing = SizingAdvisor().host_aware_size(
        {"cpu_cores": 0, "ram_total_mb": 0, "storage_available_gib": 5},
        nodes=3,
    )

    assert sizing.host_cpu == 0
    assert sizing.host_ram_mb == 0
    assert sizing.host_storage_gib == 0
    assert not sizing.fits_host()


@pytest.mark.parametrize("osds", [0, 9])
def test_invalid_osd_count_is_rejected_before_sizing(osds) -> None:
    with pytest.raises(ValueError, match="between 1 and 8"):
        SizingAdvisor().host_aware_size({}, nodes=3, ceph_disks_per_node=osds)
