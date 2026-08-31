from pathlib import Path

import pytest

from lab_ai_assistant.config import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """A deterministic config.

    Inference settings are pinned to their defaults so a developer's shell
    environment or local .env cannot change what these tests assert.
    """
    return Config(
        repo_root=Path(__file__).resolve().parents[1],
        state_dir=tmp_path,
        response_timeout=5,
        operation_timeout=5,
        inference_auto_discovery=False,
        inference_enable_thinking=False,
        inference_stream=True,
    )
