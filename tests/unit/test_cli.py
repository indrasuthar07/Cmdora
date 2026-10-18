"""Tests for the Cmdora command-line interface."""

import json

from typer.testing import CliRunner

from cmdora.cli.main import app

runner = CliRunner()


def test_help_is_available() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "secure natural-language PowerShell" in result.output
    assert "config" in result.output
    assert "history" in result.output
    assert "--dry-run" in result.output
    assert "--autonomy" in result.output
    assert "--json" in result.output
    assert "--verbose" in result.output


def test_version_is_available() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "Cmdora 0.1.0" in result.output


def test_config_init_command_is_available() -> None:
    result = runner.invoke(app, ["config", "init"])

    assert result.exit_code == 0
    assert "available in Phase 2" in result.output


def test_history_command_is_available() -> None:
    result = runner.invoke(app, ["history"])

    assert result.exit_code == 0
    assert "available after audit storage" in result.output


def test_natural_language_request_accepts_execution_flags() -> None:
    result = runner.invoke(
        app,
        [
            "list files modified today",
            "--dry-run",
            "--autonomy",
            "git",
            "--verbose",
        ],
    )

    assert result.exit_code == 0
    assert "Request received" in result.output
    assert "Dry-run enabled" in result.output
    assert "Autonomy mode requested: git" in result.output


def test_natural_language_request_can_return_json() -> None:
    result = runner.invoke(app, ["list files modified today", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["request"] == "list files modified today"
    assert payload["options"]["json_output"] is True
