"""Tests for configuration persistence across fresh store instances."""

from pathlib import Path

from cmdora.config import ApplicationConfig, ApplicationConfigStore, RepositoryConfigStore


def test_user_configuration_persists_across_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "CmdoraAI" / "config.toml"
    ApplicationConfigStore(path).save(
        ApplicationConfig(
            model="gemini-test", default_timeout_seconds=45, default_autonomy_mode="git"
        )
    )

    reloaded = ApplicationConfigStore(path).load()

    assert reloaded.model == "gemini-test"
    assert reloaded.default_timeout_seconds == 45
    assert reloaded.default_autonomy_mode == "git"


def test_repository_configuration_persists_only_in_its_own_workspace(tmp_path: Path) -> None:
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()

    assert RepositoryConfigStore(first_workspace).initialize()

    assert RepositoryConfigStore(first_workspace).load().autonomy_mode == "git"
    assert RepositoryConfigStore(second_workspace).load().autonomy_mode == "off"
