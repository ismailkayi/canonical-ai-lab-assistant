"""Read-only runtime checks for source and snap installations."""

import shutil
import subprocess
from dataclasses import dataclass

from lab_ai_assistant.ai_engine import AIEngine
from lab_ai_assistant.config import Config


@dataclass(frozen=True)
class DoctorCheck:
    """One diagnostic result."""

    name: str
    ok: bool
    detail: str


def _command_check(name: str, command: list[str]) -> DoctorCheck:
    executable = shutil.which(command[0])
    if executable is None:
        return DoctorCheck(name, False, f"{command[0]} is not available on PATH")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DoctorCheck(name, False, str(exc))

    output = (result.stdout or result.stderr).strip().splitlines()
    detail = output[0] if output else f"exit {result.returncode}"
    return DoctorCheck(name, result.returncode == 0, detail)


def run_doctor(config: Config) -> list[DoctorCheck]:
    """Inspect required tools and services without changing the host."""
    ssh_keygen = shutil.which("ssh-keygen")
    flock = shutil.which("flock")
    checks = [
        DoctorCheck(
            "Terraform working directory",
            config.terraform_dir.is_dir(),
            str(config.terraform_dir),
        ),
        _command_check("OpenTofu", ["tofu", "version"]),
        _command_check("Ansible", ["ansible", "--version"]),
        _command_check(
            "community.general",
            ["ansible-galaxy", "collection", "list", "community.general"],
        ),
        _command_check("LXD", ["lxc", "info"]),
        DoctorCheck(
            "SSH client",
            ssh_keygen is not None,
            ssh_keygen or "ssh-keygen is not available on PATH",
        ),
        DoctorCheck(
            "File locking",
            flock is not None,
            flock or "flock is not available on PATH",
        ),
    ]

    inference_available = AIEngine(config).is_available()
    checks.append(
        DoctorCheck(
            "Inference service",
            inference_available,
            (
                f"{config.inference_model} is ready at {config.inference_host}"
                if inference_available
                else f"not ready at {config.inference_host}"
            ),
        )
    )
    return checks
