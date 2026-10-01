import pytest
from typer.testing import CliRunner

from lab_ai_assistant import cli
from lab_ai_assistant.orchestrator import LabOrchestrator


@pytest.fixture
def sizing_cli(config, monkeypatch):
    state = {
        "cpu_cores": 32,
        "ram_total_mb": 128 * 1024,
        "ram_available_mb": 120 * 1024,
        "storage_available_gib": 1000,
        "consumed_cpu": 24,
        "consumed_ram_mb": 16 * 1024,
        "allocations_complete": True,
        "primary_pool": "default",
    }
    monkeypatch.setattr(cli, "get_config", lambda: config)
    monkeypatch.setattr(LabOrchestrator, "_collect_host_state", lambda _self, force=False: state)
    return CliRunner(), state


def test_shell_sizing_command_outputs_only_resolved_resources(sizing_cli) -> None:
    runner, _ = sizing_cli
    result = runner.invoke(
        cli.app,
        [
            "resolve-sizing",
            "--nodes=3",
            "--sizing-tier=medium",
            "--ceph-disks-per-node=2",
            "--local-disk-gib=10",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "8 16384 60 50"


def test_shell_partial_explicit_sizing_is_preserved(sizing_cli) -> None:
    runner, _ = sizing_cli
    result = runner.invoke(
        cli.app,
        ["resolve-sizing", "--nodes=3", "--node-cpu=6", "--ceph-disks-per-node=2"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "6 8192 40 25"


def test_shell_dataset_target_uses_all_osds_and_replication(sizing_cli) -> None:
    runner, _ = sizing_cli
    result = runner.invoke(
        cli.app,
        ["resolve-sizing", "--nodes=3", "--ceph-disks-per-node=2", "--dataset-size-gib=100"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "4 8192 40 63"


@pytest.mark.parametrize("missing", ["cpu_cores", "ram_total_mb"])
def test_shell_sizing_refuses_unmeasured_capacity(sizing_cli, missing) -> None:
    runner, state = sizing_cli
    state[missing] = 0

    result = runner.invoke(cli.app, ["resolve-sizing", "--nodes=3"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Host CPU/RAM measurements are unavailable" in result.output


def test_shell_sizing_refuses_incomplete_active_inventory(sizing_cli) -> None:
    runner, state = sizing_cli
    state["allocations_complete"] = False

    result = runner.invoke(cli.app, ["resolve-sizing", "--nodes=3"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "allocation inventory could not be read safely" in result.output


def test_shell_sizing_does_not_silently_enable_ram_fallback(sizing_cli) -> None:
    runner, state = sizing_cli
    state.update({"ram_total_mb": 64 * 1024, "consumed_ram_mb": 50 * 1024})

    result = runner.invoke(cli.app, ["resolve-sizing", "--nodes=3"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Insufficient RAM:" in result.output


def test_shell_explicit_disk_size_is_checked_against_dataset(sizing_cli) -> None:
    runner, _ = sizing_cli
    result = runner.invoke(
        cli.app,
        [
            "resolve-sizing",
            "--nodes=3",
            "--dataset-size-gib=100",
            "--ceph-disk-gib=50",
        ],
    )

    assert result.exit_code == 1
    assert "below the requested 100 GiB" in result.output
