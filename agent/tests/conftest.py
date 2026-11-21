import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOCA_CONFIG", str(tmp_path / "absent-config.toml"))
    monkeypatch.delenv("VOCA_LITELLM_KEY", raising=False)
    monkeypatch.delenv("VOCA_HERMES_URL", raising=False)
    monkeypatch.delenv("VOCA_HERMES_KEY", raising=False)
    for name in list(os.environ):
        if name.startswith("VOCA_") and "__" in name:
            monkeypatch.delenv(name)
