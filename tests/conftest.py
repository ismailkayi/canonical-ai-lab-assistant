from pathlib import Path

import pytest

from lab_ai_assistant.config import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        repo_root=Path(__file__).resolve().parents[1],
        state_dir=tmp_path,
        response_timeout=5,
        operation_timeout=5,
    )
