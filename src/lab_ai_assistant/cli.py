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


@app.callback()
def main(debug: Optional[bool] = typer.Option(None, "--debug", help="Enable debug logging")):
    """Canonical AI Lab Assistant - MicroCloud-first infrastructure automation."""
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)


if __name__ == "__main__":
    app()
