"""CLI entry point for Lab AI Assistant."""

import logging
import sys
from typing import Optional
import typer
from rich.console import Console
from lab_ai_assistant import __version__
from lab_ai_assistant.config import get_config
from lab_ai_assistant.orchestrator import LabOrchestrator
from lab_ai_assistant.ai_engine import AIEngine

app = typer.Typer(
    help="AI-powered Lab Automation Assistant for Canonical Infrastructure"
)
console = Console()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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
            console.print(f"\nTo install, run:")
            console.print(f"  sudo snap install {config.inference_engine}")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@app.command()
def version():
    """Show version information."""
    console.print(f"Canonical Lab AI Assistant v{__version__}")


@app.command()
def setup():
    """Setup inference engine."""
    config = get_config()
    console.print(f"[cyan]Setting up inference engine: {config.inference_engine}[/cyan]")
    console.print(f"\nTo install, run:")
    console.print(f"  sudo snap install {config.inference_engine}")
    console.print(f"\nAfter installation, the service should be available at:")
    console.print(f"  {config.inference_host}")
    console.print(f"\nThen start the assistant with:")
    console.print(f"  lab-ai chat")


@app.callback()
def main(
    debug: Optional[bool] = typer.Option(
        None,
        "--debug",
        help="Enable debug logging"
    )
):
    """Canonical Lab AI Assistant - Infrastructure automation with AI."""
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)


if __name__ == "__main__":
    app()
