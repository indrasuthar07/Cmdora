"""Persistent user-level application configuration."""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ApplicationConfig(BaseModel):
    """User preferences stored outside the repository."""

    model_config = ConfigDict(extra="forbid")

    model: str = "gemini-3.1-flash-lite"
    default_timeout_seconds: int = Field(default=30, gt=0)
    max_output_characters: int = Field(default=8_000, gt=0)
    default_autonomy_mode: Literal["off", "git"] = "off"
    protected_branches: list[str] = Field(default_factory=lambda: ["main", "master"])

    @field_validator("model")
    @classmethod
    def require_model_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model must not be empty")
        return normalized

    @field_validator("protected_branches")
    @classmethod
    def normalize_protected_branches(cls, branches: list[str]) -> list[str]:
        normalized: list[str] = []
        for branch in branches:
            branch_name = branch.strip()
            if not branch_name:
                raise ValueError("protected branch names must not be empty")
            if branch_name not in normalized:
                normalized.append(branch_name)
        return normalized


class ConfigError(ValueError):
    """Raised when a user configuration file cannot be read or validated."""


def default_config_path() -> Path:
    """Return the platform's standard per-user Cmdora configuration path."""
    if os.name == "nt":
        app_data = os.environ.get("APPDATA")
        base_directory = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
    else:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        base_directory = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base_directory / "CmdoraAI" / "config.toml"


def default_user_env_path() -> Path:
    """Return the persistent user-level .env path: %APPDATA%/CmdoraAI/.env."""
    return default_config_path().parent / ".env"


class ApplicationConfigStore:
    """Load and save the user's application configuration file."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.config_path = Path(config_path).expanduser() if config_path else default_config_path()

    def load(self) -> ApplicationConfig:
        """Load and validate the configuration, returning defaults when it is absent."""
        if not self.config_path.exists():
            return ApplicationConfig()

        try:
            raw_config = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
            return ApplicationConfig.model_validate(raw_config)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValidationError) as error:
            raise ConfigError(
                f"Could not load configuration at {self.config_path}: {error}"
            ) from error

    def save(self, config: ApplicationConfig) -> None:
        """Atomically persist validated settings without storing any credentials."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        content = _serialize_toml(config)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.config_path.parent,
                prefix=".config.toml.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary_path.replace(self.config_path)
        except OSError as error:
            raise ConfigError(
                f"Could not save configuration at {self.config_path}: {error}"
            ) from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def initialize(self) -> bool:
        """Create the default config once; return false when it already exists."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        config = ApplicationConfig()
        try:
            with self.config_path.open("x", encoding="utf-8", newline="\n") as config_file:
                config_file.write(_serialize_toml(config))
                config_file.flush()
                os.fsync(config_file.fileno())
        except FileExistsError:
            return False
        except OSError as error:
            raise ConfigError(
                f"Could not initialize configuration at {self.config_path}: {error}"
            ) from error
        return True


def _serialize_toml(config: ApplicationConfig) -> str:
    """Serialize the deliberately small settings model as valid TOML."""
    branches = ", ".join(
        json.dumps(branch, ensure_ascii=False) for branch in config.protected_branches
    )
    return "\n".join(
        (
            f"model = {json.dumps(config.model, ensure_ascii=False)}",
            f"default_timeout_seconds = {config.default_timeout_seconds}",
            f"max_output_characters = {config.max_output_characters}",
            f"default_autonomy_mode = {json.dumps(config.default_autonomy_mode)}",
            f"protected_branches = [{branches}]",
            "",
        )
    )
