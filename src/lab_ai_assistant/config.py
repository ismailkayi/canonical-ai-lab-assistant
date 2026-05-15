"""Configuration management for Lab AI Assistant."""

from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv


@dataclass
class Config:
    """Application configuration."""

    # Inference engine
    inference_engine: str = "nemotron-3-nano"
    inference_host: str = os.getenv("INFERENCE_HOST", "http://localhost:8000")
    inference_model: str = "nemotron-3-nano"

    # Lab automation
    lab_scripts_path: Path = Path("/home/ismail.kayi@canonical.com/lxdlab/lab-automation")
    orchestrate_script: Path = lab_scripts_path / "orchestrate.sh"

    # State management
    state_dir: Path = Path.home() / ".lab-ai-assistant"
    history_file: Path = state_dir / "deployment_history.json"
    context_file: Path = state_dir / "conversation_context.json"

    # Logging
    log_dir: Path = state_dir / "logs"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # AI settings
    max_retries: int = 3
    response_timeout: int = 300  # 5 minutes
    context_window_size: int = 4096

    # Feature flags
    use_rag: bool = False  # Future: enable RAG
    enable_confirmation: bool = True  # Ask before destructive operations

    def __post_init__(self):
        """Create necessary directories."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def get_config() -> Config:
    """Load and return configuration."""
    load_dotenv()
    return Config()
