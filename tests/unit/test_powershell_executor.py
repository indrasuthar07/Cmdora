"""Tests for workspace-confined PowerShell execution."""

import subprocess
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from cmdora.executor.powershell import PowerShellExecutor


def test_executes_without_profile_from_the_selected_workspace(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    run = mocker.patch(
        "cmdora.executor.powershell.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="completed", stderr=""
        ),
    )

    result = PowerShellExecutor(tmp_path).execute("Get-ChildItem")

    assert result.succeeded
    assert result.stdout == "completed"
    assert result.workspace_path == tmp_path.resolve()
    run.assert_called_once_with(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Get-ChildItem"],
        cwd=tmp_path.resolve(),
        capture_output=True,
        text=True,
        timeout=30.0,
        check=False,
    )


def test_captures_non_zero_exit_results(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch(
        "cmdora.executor.powershell.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="failed"),
    )

    result = PowerShellExecutor(tmp_path).execute("Get-Item missing-file")

    assert not result.succeeded
    assert result.exit_code == 1
    assert result.stderr == "failed"
    assert not result.timed_out
    assert not result.cancelled


def test_limits_stdout_and_stderr_before_returning(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch(
        "cmdora.executor.powershell.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="abcdefghij", stderr="klmnopqrst"
        ),
    )

    result = PowerShellExecutor(tmp_path, max_output_characters=5).execute("Get-Date")

    assert len(result.stdout) <= 5
    assert len(result.stderr) <= 5
    assert result.stdout_truncated
    assert result.stderr_truncated


def test_returns_timeout_result_with_captured_partial_output(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch(
        "cmdora.executor.powershell.subprocess.run",
        side_effect=subprocess.TimeoutExpired(
            cmd="powershell.exe", timeout=30, output=b"partial output", stderr=b"partial error"
        ),
    )

    result = PowerShellExecutor(tmp_path).execute("Start-Sleep -Seconds 60")

    assert not result.succeeded
    assert result.timed_out
    assert result.exit_code is None
    assert result.stdout == "partial output"
    assert result.stderr == "partial error"


def test_returns_cancelled_result_on_ctrl_c(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch("cmdora.executor.powershell.subprocess.run", side_effect=KeyboardInterrupt)

    result = PowerShellExecutor(tmp_path).execute("Start-Sleep -Seconds 60")

    assert not result.succeeded
    assert result.cancelled
    assert result.exit_code is None


def test_rejects_missing_or_file_workspace_paths(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-directory.txt"
    file_path.write_text("content")

    with pytest.raises(FileNotFoundError):
        PowerShellExecutor(tmp_path / "missing")
    with pytest.raises(NotADirectoryError):
        PowerShellExecutor(file_path)


def test_rejects_invalid_execution_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        PowerShellExecutor(tmp_path, timeout_seconds=0)
    with pytest.raises(ValueError, match="max_output_characters"):
        PowerShellExecutor(tmp_path, max_output_characters=-1)
    with pytest.raises(ValueError, match="command cannot be empty"):
        PowerShellExecutor(tmp_path).execute("   ")
