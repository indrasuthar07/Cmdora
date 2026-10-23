"""Interactive confirmation for non-read-only Cmdora actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import typer

from cmdora.safety import PolicyDecision


class ApprovalChoice(StrEnum):
    """The explicit response to an action approval prompt."""

    RUN_ONCE = "y"
    CANCEL = "n"
    DRY_RUN = "d"
    ENABLE_AUTONOMY = "a"


@dataclass(frozen=True)
class ApprovalRequest:
    """The minimum information a person needs before approving an action."""

    request: str
    plan_steps: tuple[str, ...]
    decision: PolicyDecision


def prompt_for_approval(approval: ApprovalRequest) -> ApprovalChoice:
    """Display the proposed action and return one deliberate approval choice.

    An empty response is cancellation. The function does not execute anything.
    """
    typer.echo(f"Request:\n{approval.request}\n")
    typer.echo("Plan:")
    for number, step in enumerate(approval.plan_steps, start=1):
        typer.echo(f"{number}. {step}")
    typer.echo(f"\nRisk:\n{approval.decision.risk_level.title()}\n")
    command = approval.decision.sanitized_command or "(no command)"
    typer.echo(f"Command:\n{command}\n")
    typer.echo("Options: y = run once, n = cancel, d = dry-run details, a = enable Git autonomy")
    while True:
        response = typer.prompt("Proceed? [y/N]", default="n", show_default=False)
        response = response.strip().casefold()
        if response in ApprovalChoice:
            return ApprovalChoice(response)
        typer.echo("Please choose y, n, d, or a.", err=True)


def confirm_git_autonomy() -> bool:
    """Explain the narrow Git automation boundary before persisting consent."""
    typer.echo("You are enabling Git automation for this repository.\n")
    typer.echo("Cmdora may automatically run:")
    typer.echo("- git status")
    typer.echo("- git add")
    typer.echo("- git commit")
    typer.echo("- constrained git push\n")
    typer.echo("It will not run destructive Git commands, force pushes,")
    typer.echo("system commands, package installs, or file deletion.\n")
    return typer.confirm("Continue?", default=False)


__all__ = [
    "ApprovalChoice",
    "ApprovalRequest",
    "confirm_git_autonomy",
    "prompt_for_approval",
]
