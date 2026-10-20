"""Repository-scoped configuration stored inside the selected workspace."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from cmdora.config.app_config import ConfigError


class RepositoryConfig(BaseModel):
    """Permissions and preferences that apply only to one repository."""

    model_config = ConfigDict(extra="forbid")

    autonomy_mode: Literal["off", "git"] = "off"
    allowed_write_directories: list[str] = Field(default_factory=lambda: ["."])
    protected_branches: list[str] = Field(default_factory=lambda: ["main", "master"])

    @field_validator("allowed_write_directories")
    @classmethod
    def validate_write_directories(cls, directories: list[str]) -> list[str]:
        normalized: list[str] = []
        for directory in directories:
            value = directory.strip().replace("\\", "/")
            windows_path = PureWindowsPath(value)
            path = Path(value)
            if not value or path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
                raise ValueError("allowed write directories must be non-empty relative paths")
            if ".." in path.parts or ".." in windows_path.parts:
                raise ValueError("allowed write directories cannot contain '..'")
            canonical = path.as_posix() or "."
            if canonical not in normalized:
                normalized.append(canonical)
        return normalized

    @field_validator("protected_branches")
    @classmethod
    def validate_protected_branches(cls, branches: list[str]) -> list[str]:
        normalized: list[str] = []
        for branch in branches:
            value = branch.strip()
            if not value:
                raise ValueError("protected branch names must not be empty")
            if value not in normalized:
                normalized.append(value)
        return normalized


class RepositoryConfigStore:
    """Load or initialize `.Cmdora/config.toml` under one workspace."""

    def __init__(self, workspace_path: Path | str) -> None:
        self.workspace_path = Path(workspace_path).expanduser().resolve(strict=True)
        if not self.workspace_path.is_dir():
            raise NotADirectoryError(f"Workspace path is not a directory: {self.workspace_path}")
        metadata_directory = self.workspace_path / ".Cmdora"
        if metadata_directory.is_symlink():
            raise ConfigError(
                f"Repository config directory must not be a symlink: {metadata_directory}"
            )
        self.config_path = metadata_directory / "config.toml"
        if self.config_path.is_symlink():
            raise ConfigError(f"Repository config file must not be a symlink: {self.config_path}")

    def load(self) -> RepositoryConfig:
        """Load validated repository settings, using workspace-only defaults if absent."""
        if not self.config_path.exists():
            return RepositoryConfig()
        try:
            raw_config = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
            return RepositoryConfig.model_validate(raw_config)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValidationError) as error:
            raise ConfigError(
                f"Could not load repository configuration at {self.config_path}: {error}"
            ) from error

    def initialize(self) -> bool:
        """Create a project config once with the documented starter permissions."""
        config = RepositoryConfig(
            autonomy_mode="git",
            allowed_write_directories=["src", "tests"],
            protected_branches=["main", "master"],
        )
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.config_path.open("x", encoding="utf-8", newline="\n") as config_file:
                config_file.write(_serialize_repository_config(config))
                config_file.flush()
                os.fsync(config_file.fileno())
        except FileExistsError:
            return False
        except OSError as error:
            raise ConfigError(
                f"Could not initialize repository configuration at {self.config_path}: {error}"
            ) from error
        return True


def validate_repository_write_path(
    workspace_path: Path | str,
    target_path: Path | str,
    config: RepositoryConfig,
) -> Path:
    """Resolve a write target and require it to be inside an allowed workspace directory."""
    workspace = Path(workspace_path).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise NotADirectoryError(f"Workspace path is not a directory: {workspace}")
    target = _resolve_project_path(workspace, target_path)
    if target == workspace:
        raise ValueError("A file path inside the workspace is required")

    for directory in config.allowed_write_directories:
        allowed_root = _resolve_project_path(workspace, directory)
        if target.is_relative_to(allowed_root) and target != allowed_root:
            return target
    raise PermissionError(f"Write path is not in an allowed project directory: {target}")


def _resolve_project_path(workspace: Path, path: Path | str) -> Path:
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = workspace / target
    resolved = target.resolve(strict=False)
    if not resolved.is_relative_to(workspace):
        raise ValueError(f"Path is outside the selected workspace: {path}")
    return resolved


def _serialize_repository_config(config: RepositoryConfig) -> str:
    """Serialize repository settings as TOML."""
    directories = ", ".join(
        json.dumps(value, ensure_ascii=False) for value in config.allowed_write_directories
    )
    branches = ", ".join(
        json.dumps(value, ensure_ascii=False) for value in config.protected_branches
    )
    return "\n".join(
        (
            f"autonomy_mode = {json.dumps(config.autonomy_mode)}",
            f"allowed_write_directories = [{directories}]",
            f"protected_branches = [{branches}]",
            "",
        )
    )
