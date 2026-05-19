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
    cleanup_microcloud_script: Path = scripts_dir / "cleanup_microcloud.sh"
    list_environments_script: Path = scripts_dir / "list_microcloud_environments.sh"
    scale_microcloud_script: Path = scripts_dir / "scale_microcloud.sh"
    verify_cluster_health_script: Path = scripts_dir / "verify_cluster_health.sh"
    add_cluster_node_script: Path = scripts_dir / "add_cluster_node.sh"

    inference_engine: str = "gemma4"
    inference_host: str = os.getenv("INFERENCE_HOST", "http://127.0.0.1:8336")
    inference_model: str = os.getenv("INFERENCE_MODEL", "gemma4")

    state_dir: Path = Path(os.getenv("SNAP_USER_COMMON", str(Path.home() / ".canonical-ai-lab-assistant")))
    history_file: Path = state_dir / "deployment_history.json"
    context_file: Path = state_dir / "conversation_context.json"
    log_dir: Path = state_dir / "logs"

    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    response_timeout: int = int(os.getenv("INFERENCE_TIMEOUT_SEC", "120"))
    max_retries: int = 3
    enable_confirmation: bool = True

    def __post_init__(self):
        # Resolve script root robustly for both source checkout and snap runtime.
        snap_root = os.getenv("SNAP")
        if snap_root:
            snap_repo = Path(snap_root)
            if (snap_repo / "scripts").exists():
                self.repo_root = snap_repo

        if not (self.repo_root / "scripts").exists():
            cwd_root = Path.cwd()
            if (cwd_root / "scripts").exists():
                self.repo_root = cwd_root

        self.scripts_dir = self.repo_root / "scripts"
        self.prep_host_script = self.scripts_dir / "prep_host.sh"
        self.install_inference_script = self.scripts_dir / "install_inference_snap.sh"
        self.deploy_microcloud_script = self.scripts_dir / "deploy_microcloud.sh"
        self.cleanup_microcloud_script = self.scripts_dir / "cleanup_microcloud.sh"
        self.list_environments_script = self.scripts_dir / "list_microcloud_environments.sh"
        self.scale_microcloud_script = self.scripts_dir / "scale_microcloud.sh"
        self.verify_cluster_health_script = self.scripts_dir / "verify_cluster_health.sh"
        self.add_cluster_node_script = self.scripts_dir / "add_cluster_node.sh"

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def get_config() -> Config:
    """Load and return configuration."""
    load_dotenv()
    return Config()
