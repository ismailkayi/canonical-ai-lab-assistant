"""CLI entry point for the MicroCloud-first Lab AI Assistant."""

import logging
import sys
from typing import Optional

import typer
from rich.console import Console

from lab_ai_assistant import __version__
from lab_ai_assistant.ai_engine import AIEngine
from lab_ai_assistant.config import get_config
from lab_ai_assistant.orchestrator import LabOrchestrator
from lab_ai_assistant.planning import ExecutionPlan

app = typer.Typer(help="AI-powered Lab Automation Assistant for Canonical Infrastructure")
console = Console()

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


@app.command()
def chat():
    """Start interactive chat with AI assistant."""
    try:
        config = get_config()
        orchestrator = LabOrchestrator(config)
        orchestrator.start_chat()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@app.command()
def check():
    """Check if inference engine is available."""
    try:
        config = get_config()
        ai = AIEngine(config)

        if ai.is_available():
            console.print(f"[green]✓[/green] Inference engine available at {config.inference_host}")
            console.print(f"  Model: {config.inference_model}")
        else:
            console.print(f"[red]✗[/red] Inference engine not available at {config.inference_host}")
            console.print("\nCheck that the snap service is running and INFERENCE_HOST is correct.")
            console.print("Try:")
            console.print(f"  snap services {config.inference_engine}")
            console.print(f"  curl {config.inference_host}/health")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@app.command()
def version():
    """Show version information."""
    console.print(f"Canonical AI Lab Assistant v{__version__}")


@app.command()
def bootstrap():
    """Prepare the host and install the inference snap."""
    config = get_config()
    orchestrator = LabOrchestrator(config)
    result = orchestrator.bootstrap_host()
    console.print(result)


@app.command()
def setup():
    """Show setup instructions for the inference snap."""
    config = get_config()
    console.print(f"[cyan]Inference engine:[/cyan] {config.inference_engine}")
    console.print(f"[cyan]Host prep script:[/cyan] {config.prep_host_script}")
    console.print(f"[cyan]Install script:[/cyan] {config.install_inference_script}")
    console.print(f"[cyan]Deploy script:[/cyan] {config.deploy_microcloud_script}")
    console.print("\nRun `lab-ai bootstrap` to prepare the host and install the inference snap.")
    console.print("Then run `lab-ai chat` to start the MicroCloud assistant.")


@app.command(hidden=True)
def resolve_sizing(
    nodes: int = typer.Option(3, min=3, max=50),
    sizing_tier: Optional[str] = None,
    node_cpu: Optional[int] = None,
    node_memory_mb: Optional[int] = None,
    root_disk_gib: Optional[int] = None,
    ceph_disk_gib: Optional[int] = None,
    ceph_disks_per_node: int = typer.Option(1, min=1, max=8),
    local_disk_gib: int = typer.Option(0, min=0),
    dataset_size_gib: Optional[int] = None,
    workload_description: str = "",
    storage_pool: Optional[str] = None,
):
    """Resolve resources for the shell deployer using the assistant's sizing helper."""
    parameters = {
        name: value
        for name, value in {
            "nodes": nodes,
            "sizing_tier": sizing_tier,
            "node_cpu": node_cpu,
            "node_memory_mb": node_memory_mb,
            "root_disk_gib": root_disk_gib,
            "ceph_disk_gib": ceph_disk_gib,
            "ceph_disks_per_node": ceph_disks_per_node,
            "local_disk_gib": local_disk_gib,
            "dataset_size_gib": dataset_size_gib,
            "workload_description": workload_description,
        }.items()
        if value is not None
    }
    try:
        orchestrator = LabOrchestrator(get_config())
        state = orchestrator._collect_host_state(force=True)
        if not state.get("cpu_cores") or not state.get("ram_total_mb"):
            raise RuntimeError("Host CPU/RAM measurements are unavailable.")
        capacity = orchestrator._capacity_snapshot(state, storage_pool)
        sizing = orchestrator._size_topology(parameters, capacity)
        plan = ExecutionPlan(
            action="deploy_microcloud",
            parameters=parameters,
            summary="Resolve shell deployment sizing",
            topology=sizing.topology,
            capacity=capacity,
        )
        validation = orchestrator.plan_validator.validate(plan)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
        topology = sizing.topology
        typer.echo(
            f"{topology.node_cpu} {topology.node_memory_mb} "
            f"{topology.root_disk_gib} {topology.ceph_disk_gib}"
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        logging.getLogger(__name__).error("Sizing resolution failed: %s", exc)
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.callback()
def main(debug: Optional[bool] = typer.Option(None, "--debug", help="Enable debug logging")):
    """Canonical AI Lab Assistant - MicroCloud-first infrastructure automation."""
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)


if __name__ == "__main__":
    app()
