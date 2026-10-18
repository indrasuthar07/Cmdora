"""Tests for the Phase 0 CLI scaffold."""

from typer.testing import CliRunner

from cmdora.cli.main import app

runner = CliRunner()


def test_help_is_available() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "secure natural-language PowerShell" in result.output


def test_version_is_available() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "Cmdora 0.1.0" in result.output
