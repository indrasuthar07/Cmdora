"""Shared test configuration that keeps user data isolated from the real profile."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_cmdora_user_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "app-data"))
