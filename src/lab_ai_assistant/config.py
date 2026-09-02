"""Configuration management for the MicroCloud-first assistant."""

import os
import shutil
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
    scale_microcloud_script: Path = scripts_dir / "scale_microcloud.sh"
    verify_cluster_health_script: Path = scripts_dir / "verify_cluster_health.sh"
    add_cluster_node_script: Path = scripts_dir / "add_cluster_node.sh"
    terraform_assets_dir: Path = repo_root / "terraform"
    terraform_dir: Path = repo_root / "terraform"

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
        self.scale_microcloud_script = self.scripts_dir / "scale_microcloud.sh"
        self.verify_cluster_health_script = self.scripts_dir / "verify_cluster_health.sh"
        self.add_cluster_node_script = self.scripts_dir / "add_cluster_node.sh"
        self.terraform_assets_dir = self.repo_root / "terraform"
        self.terraform_dir = self.terraform_assets_dir

        self.history_file = self.state_dir / "deployment_history.json"
        self.context_file = self.state_dir / "conversation_context.json"
        self.log_dir = self.state_dir / "logs"

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        if snap_root:
            self.terraform_dir = self.state_dir / "terraform"
            self._sync_terraform_assets()

    def _sync_terraform_assets(self) -> None:
        """Copy immutable Terraform configuration into the writable snap state."""
        if not self.terraform_assets_dir.is_dir():
            raise FileNotFoundError(
                f"Terraform assets directory not found: {self.terraform_assets_dir}"
            )

        assets = sorted(self.terraform_assets_dir.rglob("*.tf"))
        lock_file = self.terraform_assets_dir / ".terraform.lock.hcl"
        if lock_file.is_file():
            assets.append(lock_file)
        if not assets:
            raise FileNotFoundError(
                f"No Terraform configuration found in {self.terraform_assets_dir}"
            )

        for source in assets:
            relative_path = source.relative_to(self.terraform_assets_dir)
            destination = self.terraform_dir / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_file() and destination.read_bytes() == source.read_bytes():
                continue
            temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
            shutil.copy2(source, temporary)
            temporary.replace(destination)


def get_config() -> Config:
    """Load and return configuration."""
    load_dotenv()
    return Config()
