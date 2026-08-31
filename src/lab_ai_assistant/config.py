"""Configuration management for the MicroCloud-first assistant."""

import os
from dataclasses import dataclass, field
from pathlib import Path

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
    list_orphaned_projects_script: Path = scripts_dir / "list_orphaned_lxd_projects.sh"
    cleanup_orphaned_project_script: Path = scripts_dir / "cleanup_orphaned_lxd_project.sh"
    scale_microcloud_script: Path = scripts_dir / "scale_microcloud.sh"
    verify_cluster_health_script: Path = scripts_dir / "verify_cluster_health.sh"
    add_cluster_node_script: Path = scripts_dir / "add_cluster_node.sh"

    inference_engine: str = field(default_factory=lambda: os.getenv("INFERENCE_ENGINE", "gemma4"))
    inference_auto_discovery: bool = field(
        default_factory=lambda: (
            os.getenv("INFERENCE_AUTO_DISCOVERY", "true").lower() not in {"0", "false", "no", "off"}
        )
    )
    inference_host: str = field(
        default_factory=lambda: os.getenv("INFERENCE_HOST", "http://127.0.0.1:8336")
    )
    inference_model: str = field(default_factory=lambda: os.getenv("INFERENCE_MODEL", "gemma4"))

    state_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "SNAP_USER_COMMON",
                str(Path.home() / ".canonical-ai-lab-assistant"),
            )
        )
    )
    history_file: Path = field(init=False)
    context_file: Path = field(init=False)
    log_dir: Path = field(init=False)

    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    response_timeout: int = field(
        default_factory=lambda: int(os.getenv("INFERENCE_TIMEOUT_SEC", "120"))
    )
    inference_max_output_tokens: int = field(
        default_factory=lambda: int(os.getenv("INFERENCE_MAX_OUTPUT_TOKENS", "512"))
    )
    # Thinking models (for example Gemma 4) otherwise spend the whole output
    # budget on hidden reasoning and return an empty answer. Keep it off so the
    # assistant stays interactive.
    inference_enable_thinking: bool = field(
        default_factory=lambda: (
            os.getenv("INFERENCE_ENABLE_THINKING", "false").lower() in {"1", "true", "yes", "on"}
        )
    )
    # Streaming only changes how the answer is delivered, not what is generated.
    # It exists so the first words appear immediately instead of after the whole
    # response has been produced.
    inference_stream: bool = field(
        default_factory=lambda: (
            os.getenv("INFERENCE_STREAM", "true").lower() not in {"0", "false", "no", "off"}
        )
    )
    inference_restart_timeout: float = field(
        default_factory=lambda: float(os.getenv("INFERENCE_RESTART_TIMEOUT_SEC", "15"))
    )
    max_retries: int = field(default_factory=lambda: int(os.getenv("INFERENCE_MAX_RETRIES", "3")))
    operation_timeout: int = field(
        default_factory=lambda: int(os.getenv("OPERATION_TIMEOUT_SEC", "3600"))
    )

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
        self.list_orphaned_projects_script = self.scripts_dir / "list_orphaned_lxd_projects.sh"
        self.cleanup_orphaned_project_script = self.scripts_dir / "cleanup_orphaned_lxd_project.sh"
        self.scale_microcloud_script = self.scripts_dir / "scale_microcloud.sh"
        self.verify_cluster_health_script = self.scripts_dir / "verify_cluster_health.sh"
        self.add_cluster_node_script = self.scripts_dir / "add_cluster_node.sh"

        self.history_file = self.state_dir / "deployment_history.json"
        self.context_file = self.state_dir / "conversation_context.json"
        self.log_dir = self.state_dir / "logs"

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def get_config() -> Config:
    """Load and return configuration."""
    load_dotenv()
    return Config()
