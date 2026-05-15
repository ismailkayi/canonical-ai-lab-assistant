"""Configuration management for the MicroCloud-first assistant."""

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


@dataclass
class Config:
    """Application configuration."""

    repo_root: Path = Path(__file__).resolve().parents[2]
    scripts_dir: Path = repo_root / "scripts"
    prep_host_script: Path = scripts_dir / "prep_host.sh"
    install_inference_script: Path = scripts_dir / "install_inference_snap.sh"
    deploy_microcloud_script: Path = scripts_dir / "deploy_microcloud.sh"

    inference_engine: str = "nemotron-3-nano"
    inference_host: str = os.getenv("INFERENCE_HOST", "http://localhost:8000")
    inference_model: str = os.getenv("INFERENCE_MODEL", "nemotron-3-nano")

    state_dir: Path = Path.home() / ".canonical-ai-lab-assistant"
    history_file: Path = state_dir / "deployment_history.json"
    context_file: Path = state_dir / "conversation_context.json"
    log_dir: Path = state_dir / "logs"

    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    response_timeout: int = 300
    max_retries: int = 3
    enable_confirmation: bool = True

    def __post_init__(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def get_config() -> Config:
    """Load and return configuration."""
    load_dotenv()
    return Config()
