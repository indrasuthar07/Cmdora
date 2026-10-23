"""The Cmdora command-line entry point."""

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import monotonic
from typing import Annotated, Any
from uuid import uuid4

import typer
from rich.console import Console
from typer.core import TyperGroup

from cmdora import __version__
from cmdora.cli.approval import (
    ApprovalChoice,
    ApprovalRequest,
    confirm_git_autonomy,
    prompt_for_approval,
)
from cmdora.config import (
    ApplicationConfigStore,
    ConfigError,
    RepositoryConfigStore,
    validate_repository_write_path,
)
from cmdora.context import SafeContext
from cmdora.executor import CommandResult, DeterministicOperations
from cmdora.llm import PlannerProvider
from cmdora.llm.gemini import GeminiPlanner, PlannerError
from cmdora.safety import (
    GitSafetyValidator,
    PolicyDecision,
    assess_command,
    format_policy_decision,
)
from cmdora.schemas import ActionPlan
from cmdora.storage import AuditLog, AuditLogError, AuditRecord


@dataclass(frozen=True)
class RequestOptions:
    """User-selected execution options passed to future workflow phases."""

    dry_run: bool
    autonomy: str | None
    json_output: bool
    verbose: bool


class NaturalLanguageGroup(TyperGroup):
    """Treat unknown root text as a natural-language request, not a command name."""

    def invoke(self, ctx: Any) -> Any:
        if ctx._protected_args:
            arguments = [*ctx._protected_args, *ctx.args]
            if self.get_command(ctx, arguments[0]) is None:
                ctx._protected_args = []
                ctx.args = arguments
        return super().invoke(ctx)


app = typer.Typer(
    name="Cmdora",
    cls=NaturalLanguageGroup,
    help="A secure natural-language PowerShell and Git assistant.",
    no_args_is_help=True,
    invoke_without_command=True,
    add_completion=False,
    context_settings={"allow_extra_args": True},
)
config_app = typer.Typer(help="Manage Cmdora configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")
history_app = typer.Typer(
    help="Browse command and operation audit history.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(history_app, name="history")
console = Console()


def _emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, default=str))


def _request_approval(
    *,
    request: str,
    plan_steps: tuple[str, ...],
    decision: PolicyDecision,
    options: RequestOptions,
    request_id: str,
    workspace_path: Path,
    autonomous: bool = False,
) -> ApprovalChoice:
    """Get explicit consent before a non-read-only action reaches an executor."""
    if autonomous:
        return ApprovalChoice.RUN_ONCE
    if options.json_output:
        _record_audit(
            command=None,
            exit_code=None,
            duration_seconds=0,
            final_status="approval_required",
            policy_decision=decision.model_dump_json(),
            approval_status="pending",
            request_id=request_id,
            user_request=request,
            workspace_identifier=workspace_path,
        )
        _emit_json(
            {
                "status": "approval_required",
                "policy": decision.model_dump(mode="json"),
                "plan": list(plan_steps),
            }
        )
        return ApprovalChoice.CANCEL

    choice = prompt_for_approval(ApprovalRequest(request, plan_steps, decision))
    if choice == ApprovalChoice.ENABLE_AUTONOMY:
        if not confirm_git_autonomy():
            choice = ApprovalChoice.CANCEL
        else:
            RepositoryConfigStore(workspace_path).set_autonomy_mode("git")
            typer.echo("Git autonomy enabled for this repository only. Running this action once.")
    if choice == ApprovalChoice.CANCEL:
        _record_audit(
            command=None,
            exit_code=None,
            duration_seconds=0,
            final_status="cancelled",
            policy_decision=decision.model_dump_json(),
            approval_status="cancelled",
            request_id=request_id,
            user_request=request,
            workspace_identifier=workspace_path,
        )
        typer.echo("Cancelled — no command executed.")
    return choice


def _approval_status(choice: ApprovalChoice, *, autonomous: bool = False) -> str:
    """Return the audit label corresponding to an interactive consent choice."""
    if autonomous:
        return "autonomous"
    return "autonomy_enabled" if choice == ApprovalChoice.ENABLE_AUTONOMY else "approved_once"


def _planner_dotenv_path(workspace_path: Path) -> Path:
    """Find a local API-key file without searching outside known Cmdora locations.

    A selected workspace owns its own configuration.  When running Cmdora from a source checkout
    against a disposable demo workspace, the checkout's ``.env`` is a deliberate fallback so the
    user does not need to copy an API key into every demo directory.  Installed packages retain
    the workspace-only behaviour because they have no checked-out ``pyproject.toml``.
    """
    workspace_dotenv = workspace_path / ".env"
    if workspace_dotenv.is_file():
        return workspace_dotenv

    checkout_root = Path(__file__).resolve().parents[3]
    checkout_dotenv = checkout_root / ".env"
    if (checkout_root / "pyproject.toml").is_file() and checkout_dotenv.is_file():
        return checkout_dotenv
    return workspace_dotenv


def _safe_planning_context(workspace_path: Path) -> SafeContext:
    """Collect only the allowlisted, redacted metadata needed for a plan."""
    operations = DeterministicOperations(workspace_path)
    status_result = operations.git_status()
    is_git_repository = status_result.succeeded
    status = status_result.stdout if is_git_repository else ""
    branch: str | None = None
    if status:
        first_line = status.splitlines()[0]
        if first_line.startswith("## "):
            branch = first_line.removeprefix("## ").split("...", maxsplit=1)[0]
            if branch.startswith("No commits yet on "):
                branch = branch.removeprefix("No commits yet on ")
            if branch == "HEAD (no branch)":
                branch = None

    repository_config = RepositoryConfigStore(workspace_path).load()
    return SafeContext(
        workspace_basename=workspace_path.name or "workspace",
        is_git_repository=is_git_repository,
        git_branch=branch,
        git_status=status,
        supported_actions=[
            "inspect",
            "create_file",
            "list_modified_today",
            "git_status",
            "git_add",
            "git_commit",
            "git_push",
            "shell_command",
            "clarification",
        ],
        autonomy_mode=repository_config.autonomy_mode,
    )


def _display_action_plan(plan: ActionPlan, *, json_output: bool) -> None:
    """Present a plan as a proposal; this function never executes its steps."""
    status = "needs_clarification" if plan.needs_clarification else "planned"
    if json_output:
        _emit_json({"status": status, "plan": plan.model_dump(mode="json")})
        return

    console.print(f"[bold]Plan[/bold] — {plan.summary}")
    if plan.needs_clarification:
        console.print(f"[yellow]Clarification needed:[/yellow] {plan.clarifying_question}")
        return
    for step in plan.steps:
        console.print(f"{step.id}. [{step.kind}] {step.purpose}")
        if step.command:
            console.print(f"   Proposed PowerShell: {step.command}")
        console.print(f"   Effect: {step.expected_effect}")
        console.print(f"   Risk: {step.risk_hint}")
    console.print("Plan only — no proposed action was executed.")


def _emit_blocked_policy(
    *,
    decision: PolicyDecision,
    options: RequestOptions,
    request_id: str,
    user_request: str,
    workspace_path: Path,
) -> None:
    """Record and display a rejected policy decision before any executor call."""
    _record_audit(
        command=None,
        exit_code=None,
        duration_seconds=0,
        final_status="blocked",
        policy_decision=decision.model_dump_json(),
        approval_status="not_applicable",
        request_id=request_id,
        user_request=user_request,
        workspace_identifier=workspace_path,
    )
    if options.json_output:
        _emit_json({"status": "blocked", "policy": decision.model_dump(mode="json")})
    else:
        typer.echo(format_policy_decision(decision), err=True)


def _handle_planned_request(
    request: str,
    options: RequestOptions,
    request_id: str,
    *,
    planner: PlannerProvider | None = None,
    workspace_path: Path | None = None,
) -> ActionPlan:
    """Plan an otherwise unsupported request and record it without execution."""
    workspace = (workspace_path or Path.cwd()).resolve()
    app_config = ApplicationConfigStore().load()
    active_planner = planner or GeminiPlanner(
        model=app_config.model,
        dotenv_path=_planner_dotenv_path(workspace),
    )
    context = _safe_planning_context(workspace)
    plan = active_planner.create_plan(request, context)
    _record_audit(
        command=None,
        exit_code=None,
        duration_seconds=0,
        final_status="needs_clarification" if plan.needs_clarification else "planned",
        policy_decision="not_evaluated",
        approval_status="not_required",
        request_id=request_id,
        user_request=request,
        workspace_identifier=workspace,
        llm_plan=plan.model_dump_json(),
    )
    _display_action_plan(plan, json_output=options.json_output)
    return plan


def _record_audit(
    *,
    command: str | None,
    exit_code: int | None,
    duration_seconds: float,
    stdout: str = "",
    stderr: str = "",
    final_status: str,
    policy_decision: str = "allowed",
    approval_status: str = "not_required",
    request_id: str | None = None,
    user_request: str = "",
    workspace_identifier: str | Path | None = None,
    llm_plan: str | None = None,
) -> None:
    AuditLog().append(
        AuditRecord(
            policy_decision=policy_decision,
            approval_status=approval_status,
            executed_command=command,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
            stdout=stdout,
            stderr=stderr,
            final_status=final_status,
            request_id=request_id or str(uuid4()),
            user_request=user_request,
            workspace_identifier=str(Path(workspace_identifier or Path.cwd()).resolve()),
            llm_plan=llm_plan,
        )
    )


def _record_command_result(
    result: CommandResult,
    *,
    request_id: str,
    user_request: str,
    approval_status: str = "not_required",
) -> None:
    status = (
        "timed_out"
        if result.timed_out
        else "cancelled"
        if result.cancelled
        else "succeeded"
        if result.succeeded
        else "failed"
    )
    _record_audit(
        command=" ".join(result.command),
        exit_code=result.exit_code,
        duration_seconds=result.elapsed_seconds,
        stdout=result.stdout,
        stderr=result.stderr,
        final_status=status,
        approval_status=approval_status,
        request_id=request_id,
        user_request=user_request,
        workspace_identifier=result.workspace_path,
    )


def _create_file_path(request: str) -> str | None:
    """Return the target path for the supported deterministic create-file phrasing."""
    patterns = (
        r"^(?:make|create)\s+(?:a\s+)?(?:new\s+)?file\s+(?:named|called)\s+(.+?)\s*[.!]?$",
        r"^create\s+(.+\.[A-Za-z0-9]+)\s*$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, request.strip(), re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("\"'")
    return None


def _powershell_literal(value: str) -> str:
    escaped = value.replace("`", "``").replace("$", "`$").replace('"', '`"')
    return f'"{escaped}"'


def _display_path(root: Path, target: Path) -> str:
    relative = target.relative_to(root)
    return ".\\" + str(relative).replace("/", "\\")


def _dry_run_create_file(
    path: str,
    workspace_path: Path,
    json_output: bool,
    *,
    request_id: str,
    user_request: str,
) -> None:
    config = RepositoryConfigStore(workspace_path).load()
    target = validate_repository_write_path(workspace_path, path, config)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"File already exists: {target}")
    relative_path = _display_path(workspace_path, target)
    command = f"New-Item -ItemType File -Path {_powershell_literal(relative_path)}"
    extension = target.suffix.lower()
    file_type = {
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".py": "Python",
        ".html": "HTML",
        ".css": "CSS",
        ".json": "JSON",
        ".md": "Markdown",
    }.get(extension, "")
    description = f"{file_type + ' ' if file_type else ''}file"
    effect = f"Creates a new empty {description} in the current workspace."
    _record_audit(
        command=None,
        exit_code=None,
        duration_seconds=0,
        final_status="dry_run",
        request_id=request_id,
        user_request=user_request,
        workspace_identifier=workspace_path,
    )
    if json_output:
        _emit_json(
            {
                "status": "dry_run",
                "proposed_command": command,
                "effect": effect,
            }
        )
        return

    typer.echo("Dry run only — no command executed.\n")
    typer.echo(f"Proposed command:\n{command}\n")
    typer.echo(f"Effect:\n{effect}")


def _emit_command_result(
    result: CommandResult,
    json_output: bool,
    *,
    request_id: str,
    user_request: str,
    record_audit: bool = True,
    approval_status: str = "not_required",
) -> None:
    if record_audit:
        _record_command_result(
            result,
            request_id=request_id,
            user_request=user_request,
            approval_status=approval_status,
        )
    if json_output:
        _emit_json({"status": "success" if result.succeeded else "failed", **asdict(result)})
        return
    output = result.stdout.strip()
    error = result.stderr.strip()
    if output:
        typer.echo(output)
    if error:
        typer.echo(error, err=True)
    if result.timed_out:
        typer.echo("Command timed out.", err=True)
    elif result.cancelled:
        typer.echo("Command cancelled.", err=True)
    elif not result.succeeded and not error:
        typer.echo(f"Command failed with exit code {result.exit_code}.", err=True)
    if not output and not error and result.succeeded:
        typer.echo("Command completed successfully.")
    if not result.succeeded:
        raise typer.Exit(code=1)


def _dry_run_message(
    command: str,
    effect: str,
    json_output: bool,
    *,
    request_id: str,
    user_request: str,
    workspace_path: Path,
) -> None:
    _record_audit(
        command=None,
        exit_code=None,
        duration_seconds=0,
        final_status="dry_run",
        request_id=request_id,
        user_request=user_request,
        workspace_identifier=workspace_path,
    )
    if json_output:
        _emit_json({"status": "dry_run", "proposed_command": command, "effect": effect})
        return
    typer.echo("Dry run only — no command executed.\n")
    typer.echo(f"Proposed command:\n{command}\n")
    typer.echo(f"Effect:\n{effect}")


def _handle_deterministic_request(request: str, options: RequestOptions, request_id: str) -> bool:
    """Run a supported deterministic request; return false when no route matches."""
    workspace_path = Path.cwd().resolve()
    if options.autonomy not in {None, "off", "git"}:
        raise ValueError("--autonomy must be either 'off' or 'git'.")
    repository_config = RepositoryConfigStore(workspace_path).load()
    autonomy_enabled = options.autonomy == "git"
    if autonomy_enabled:
        if repository_config.autonomy_mode != options.autonomy:
            raise PermissionError(
                f"Autonomy mode '{options.autonomy}' is not enabled for this repository. "
                "Approve an action with 'a' or explicitly set autonomy_mode = 'git' in "
                ".Cmdora/config.toml."
            )
        if not options.json_output:
            typer.echo(f"Autonomy: git mode enabled for repository {workspace_path.name}")
    operations = DeterministicOperations(workspace_path)

    file_path = _create_file_path(request)
    if file_path is not None:
        if options.dry_run:
            _dry_run_create_file(
                file_path,
                workspace_path,
                options.json_output,
                request_id=request_id,
                user_request=request,
            )
            return True
        target = validate_repository_write_path(workspace_path, file_path, repository_config)
        relative_path = _display_path(workspace_path, target)
        command = f"New-Item -ItemType File -Path {_powershell_literal(relative_path)}"
        policy = assess_command(
            command,
            workspace_path,
            repository_config=repository_config,
        )
        if not policy.allowed:
            _emit_blocked_policy(
                decision=policy.decision,
                options=options,
                request_id=request_id,
                user_request=request,
                workspace_path=workspace_path,
            )
            raise typer.Exit(code=1)
        approval = _request_approval(
            request=request,
            plan_steps=(
                f"Check whether {_display_path(workspace_path, target)} already exists.",
                "Create the file only if it does not exist.",
            ),
            decision=policy.decision,
            options=options,
            request_id=request_id,
            workspace_path=workspace_path,
            autonomous=autonomy_enabled,
        )
        if approval == ApprovalChoice.CANCEL:
            return True
        if approval == ApprovalChoice.DRY_RUN:
            _dry_run_create_file(
                file_path,
                workspace_path,
                options.json_output,
                request_id=request_id,
                user_request=request,
            )
            return True
        started_at = monotonic()
        created_path = operations.create_file(file_path)
        _record_audit(
            command=f"create_file({file_path!r})",
            exit_code=0,
            duration_seconds=monotonic() - started_at,
            stdout=f"Created file: {_display_path(workspace_path, created_path)}",
            final_status="succeeded",
            approval_status=_approval_status(approval, autonomous=autonomy_enabled),
            request_id=request_id,
            user_request=request,
            workspace_identifier=workspace_path,
        )
        if options.json_output:
            _emit_json({"status": "success", "created_file": str(created_path)})
        else:
            typer.echo(f"Created file: {_display_path(workspace_path, created_path)}")
        return True

    if re.fullmatch(r"(?:list\s+)?(?:all\s+)?files\s+modified\s+today", request.strip(), re.I):
        if options.dry_run:
            _dry_run_message(
                "Get-ChildItem -File -Recurse",
                "Lists workspace files modified today without changing them.",
                options.json_output,
                request_id=request_id,
                user_request=request,
                workspace_path=workspace_path,
            )
            return True
        started_at = monotonic()
        files = operations.list_modified_today()
        _record_audit(
            command="list_modified_today()",
            exit_code=0,
            duration_seconds=monotonic() - started_at,
            stdout=f"{len(files)} files modified today",
            final_status="succeeded",
            request_id=request_id,
            user_request=request,
            workspace_identifier=workspace_path,
        )
        if options.json_output:
            _emit_json({"status": "success", "files": [asdict(item) for item in files]})
        elif not files:
            typer.echo("No files modified today.")
        else:
            for item in files:
                modified_at = item.modified_at.strftime("%H:%M:%S")
                typer.echo(f"{modified_at}  {item.size_bytes:>8} bytes  {item.path}")
        return True

    if re.fullmatch(r"(?:show\s+)?git\s+status", request.strip(), re.I):
        if options.dry_run:
            _dry_run_message(
                "git status --short --branch",
                "Shows the current branch and working-tree changes.",
                options.json_output,
                request_id=request_id,
                user_request=request,
                workspace_path=workspace_path,
            )
        else:
            _emit_command_result(
                operations.git_status(),
                options.json_output,
                request_id=request_id,
                user_request=request,
            )
        return True

    commit_match = re.search(r"\bcommit\b.*?\bwith\s+message\s+(.+)$", request.strip(), re.I)
    if commit_match:
        message = commit_match.group(1).strip().strip("\"'")
        if not message:
            raise ValueError("Please provide a non-empty commit message.")
        if options.dry_run:
            _dry_run_message(
                f"git commit -m {_powershell_literal(message)}",
                "Commits the files already staged in the current workspace.",
                options.json_output,
                request_id=request_id,
                user_request=request,
                workspace_path=workspace_path,
            )
            return True
        policy = assess_command(
            f"git commit -m {_powershell_literal(message)}",
            workspace_path,
            autonomy_mode=options.autonomy or "off",
        )
        if not policy.allowed:
            _emit_blocked_policy(
                decision=policy.decision,
                options=options,
                request_id=request_id,
                user_request=request,
                workspace_path=workspace_path,
            )
            raise typer.Exit(code=1)
        approval = _request_approval(
            request=request,
            plan_steps=(
                "Check that the selected Git repository has staged changes.",
                "Create a commit using the supplied message.",
            ),
            decision=policy.decision,
            options=options,
            request_id=request_id,
            workspace_path=workspace_path,
            autonomous=autonomy_enabled,
        )
        if approval == ApprovalChoice.CANCEL:
            return True
        if approval == ApprovalChoice.DRY_RUN:
            _dry_run_message(
                f"git commit -m {_powershell_literal(message)}",
                "Commits the files already staged in the current workspace.",
                options.json_output,
                request_id=request_id,
                user_request=request,
                workspace_path=workspace_path,
            )
            return True
        commit_result = operations.git_commit(message)
        _emit_command_result(
            commit_result,
            options.json_output,
            request_id=request_id,
            user_request=request,
            approval_status=_approval_status(approval, autonomous=autonomy_enabled),
        )
        return True

    if re.fullmatch(
        r"(?:(?:push\s+)?(?:my\s+)?(?:current\s+)?branch(?:\s+to\s+origin)?|git\s+push(?:\s+origin)?)",
        request.strip(),
        re.I,
    ):
        if options.dry_run:
            _dry_run_message(
                "git push origin HEAD",
                "Pushes the current branch to the origin remote.",
                options.json_output,
                request_id=request_id,
                user_request=request,
                workspace_path=workspace_path,
            )
        else:
            origin_url = GitSafetyValidator(workspace_path).inspect().origin_url
            policy = assess_command(
                "git push origin HEAD",
                workspace_path,
                explicit_push_request=True,
                expected_origin_url=origin_url,
                protected_branches=repository_config.protected_branches,
            )
            if not policy.allowed:
                _emit_blocked_policy(
                    decision=policy.decision,
                    options=options,
                    request_id=request_id,
                    user_request=request,
                    workspace_path=workspace_path,
                )
                raise typer.Exit(code=1)
            approval = _request_approval(
                request=request,
                plan_steps=(
                    "Verify the current non-protected branch and origin remote.",
                    "Push the current branch to origin.",
                ),
                decision=policy.decision,
                options=options,
                request_id=request_id,
                workspace_path=workspace_path,
                autonomous=autonomy_enabled,
            )
            if approval == ApprovalChoice.CANCEL:
                return True
            if approval == ApprovalChoice.DRY_RUN:
                _dry_run_message(
                    "git push origin HEAD",
                    "Pushes the current branch to the origin remote.",
                    options.json_output,
                    request_id=request_id,
                    user_request=request,
                    workspace_path=workspace_path,
                )
                return True
            _emit_command_result(
                operations.git_push(),
                options.json_output,
                request_id=request_id,
                user_request=request,
                approval_status=_approval_status(approval, autonomous=autonomy_enabled),
            )
        return True

    return False


def _extract_request_options(
    arguments: list[str], initial_options: RequestOptions
) -> tuple[str, RequestOptions]:
    """Parse supported root flags that appear after a natural-language request."""
    options = initial_options
    request_parts: list[str] = []
    index = 0

    while index < len(arguments):
        argument = arguments[index]
        if argument == "--dry-run":
            options = replace(options, dry_run=True)
        elif argument == "--json":
            options = replace(options, json_output=True)
        elif argument == "--verbose":
            options = replace(options, verbose=True)
        elif argument == "--autonomy":
            index += 1
            if index >= len(arguments):
                raise typer.BadParameter("--autonomy requires a mode, for example: --autonomy git")
            options = replace(options, autonomy=arguments[index])
        elif argument.startswith("--autonomy="):
            options = replace(options, autonomy=argument.removeprefix("--autonomy="))
        else:
            request_parts.append(argument)
        index += 1

    return " ".join(request_parts), options


@app.callback()
def main(
    ctx: typer.Context,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview a future action without executing it."),
    ] = False,
    autonomy: Annotated[
        str | None,
        typer.Option("--autonomy", help="Request an autonomy mode, such as 'git'."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the command result as JSON."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Show additional command details."),
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the installed Cmdora version and exit."),
    ] = False,
) -> None:
    """Plan and run approved PowerShell and Git workflows."""
    if version:
        console.print(f"Cmdora {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None and ctx.args:
        request, options = _extract_request_options(
            ctx.args,
            RequestOptions(
                dry_run=dry_run,
                autonomy=autonomy,
                json_output=json_output,
                verbose=verbose,
            ),
        )
        if request:
            request_id = str(uuid4())
            workspace_path = Path.cwd().resolve()
            request_started_at = monotonic()
            try:
                handled = _handle_deterministic_request(request, options, request_id)
            except (KeyboardInterrupt, typer.Abort):
                try:
                    _record_audit(
                        command=None,
                        exit_code=None,
                        duration_seconds=monotonic() - request_started_at,
                        final_status="cancelled",
                        policy_decision="not_evaluated",
                        approval_status="cancelled",
                        request_id=request_id,
                        user_request=request,
                        workspace_identifier=workspace_path,
                    )
                except AuditLogError:
                    pass
                typer.echo("Cancelled — no subsequent action was executed.", err=True)
                raise typer.Exit(code=130) from None
            except AuditLogError as error:
                typer.echo(f"Audit error: {error}", err=True)
                raise typer.Exit(code=1) from error
            except (OSError, ValueError) as error:
                try:
                    _record_audit(
                        command=request,
                        exit_code=1,
                        duration_seconds=monotonic() - request_started_at,
                        stderr=str(error),
                        final_status="denied" if isinstance(error, PermissionError) else "failed",
                        policy_decision="denied"
                        if isinstance(error, PermissionError)
                        else "allowed",
                        request_id=request_id,
                        user_request=request,
                        workspace_identifier=workspace_path,
                    )
                except AuditLogError:
                    pass
                typer.echo(f"Error: {error}", err=True)
                raise typer.Exit(code=1) from error
            if not handled:
                try:
                    _handle_planned_request(request, options, request_id)
                except (KeyboardInterrupt, typer.Abort):
                    try:
                        _record_audit(
                            command=None,
                            exit_code=None,
                            duration_seconds=monotonic() - request_started_at,
                            final_status="cancelled",
                            policy_decision="not_evaluated",
                            approval_status="cancelled",
                            request_id=request_id,
                            user_request=request,
                            workspace_identifier=workspace_path,
                        )
                    except AuditLogError:
                        pass
                    typer.echo("Cancelled — no subsequent action was executed.", err=True)
                    raise typer.Exit(code=130) from None
                except AuditLogError as error:
                    typer.echo(f"Audit error: {error}", err=True)
                    raise typer.Exit(code=1) from error
                except PlannerError as error:
                    try:
                        _record_audit(
                            command=None,
                            exit_code=None,
                            duration_seconds=monotonic() - request_started_at,
                            stderr=str(error),
                            final_status="planning_failed",
                            policy_decision="not_evaluated",
                            request_id=request_id,
                            user_request=request,
                            workspace_identifier=workspace_path,
                        )
                    except AuditLogError:
                        pass
                    typer.echo(f"Planning error: {error}", err=True)
                    raise typer.Exit(code=1) from error
                except (OSError, ValueError) as error:
                    try:
                        _record_audit(
                            command=None,
                            exit_code=1,
                            duration_seconds=monotonic() - request_started_at,
                            stderr=str(error),
                            final_status="failed",
                            policy_decision="denied"
                            if isinstance(error, PermissionError)
                            else "not_evaluated",
                            request_id=request_id,
                            user_request=request,
                            workspace_identifier=workspace_path,
                        )
                    except AuditLogError:
                        pass
                    typer.echo(f"Planning error: {error}", err=True)
                    raise typer.Exit(code=1) from error


@config_app.command("init")
def config_init(
    project: Annotated[
        bool,
        typer.Option("--project", help="Initialize config in .Cmdora/ for the current workspace."),
    ] = False,
) -> None:
    """Create user-level config, or project-scoped config with --project."""
    store = RepositoryConfigStore(Path.cwd()) if project else ApplicationConfigStore()
    try:
        created = store.initialize()
    except ConfigError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    if created:
        scope = "project" if project else "user"
        console.print(f"Created {scope} configuration: {store.config_path}")
    else:
        console.print(f"Configuration already exists; left unchanged: {store.config_path}")


def _display_history_records(records: list[dict[str, Any]]) -> None:
    for record in records:
        console.print(
            f"[{record.get('timestamp', 'unknown time')}] "
            f"Request ID: {record.get('request_id', 'unknown')} | "
            f"{record.get('final_status', 'unknown')} "
            f"(policy: {record.get('policy_decision', 'unknown')}; "
            f"approval: {record.get('approval_status', 'unknown')})"
        )
        console.print(f"  Request: {record.get('user_request') or '(not recorded)'}")
        console.print(f"  Workspace: {record.get('workspace_identifier') or '(not recorded)'}")
        console.print(f"  LLM plan: {record.get('llm_plan') or '(not available)'}")
        command = record.get("executed_command")
        if command:
            console.print(f"  Command: {command}")
        console.print(
            f"  Exit: {record.get('exit_code')}; Duration: {record.get('duration_seconds', 0):.3f}s"
        )
        if record.get("stdout"):
            console.print(f"  stdout: {record['stdout']}")
        if record.get("stderr"):
            console.print(f"  stderr: {record['stderr']}")


@history_app.callback()
def history(
    ctx: typer.Context,
    last: Annotated[int, typer.Option("--last", min=1, help="Show the latest N records.")] = 20,
) -> None:
    """Show recent command and operation audit records."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        records = AuditLog().recent(limit=last)
    except AuditLogError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error
    if not records:
        console.print("No command history yet.")
        return
    _display_history_records(records)


@history_app.command("show")
def history_show(request_id: str) -> None:
    """Show every audit record for one request ID."""
    try:
        records = AuditLog().by_request_id(request_id)
    except AuditLogError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error
    if not records:
        typer.echo(f"No history found for request ID: {request_id}", err=True)
        raise typer.Exit(code=1)
    _display_history_records(records)


if __name__ == "__main__":
    app()
