"""The Cmdora command-line entry point."""

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import monotonic
from typing import Annotated, Any

import typer
from rich.console import Console
from typer.core import TyperGroup

from cmdora import __version__
from cmdora.config import (
    ApplicationConfigStore,
    ConfigError,
    RepositoryConfigStore,
    validate_repository_write_path,
)
from cmdora.executor import CommandResult, DeterministicOperations
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
console = Console()


def _print_unsupported_request(request: str, options: RequestOptions) -> None:
    """Explain that a request does not match a supported deterministic operation."""
    _record_audit(
        command=None,
        exit_code=None,
        duration_seconds=0,
        final_status="unsupported",
        policy_decision="not_evaluated",
    )
    payload = {
        "status": "unsupported",
        "request": request,
        "options": asdict(options),
        "message": "This request is not supported by the current deterministic CLI.",
    }

    if options.json_output:
        typer.echo(json.dumps(payload))
        return

    console.print("[bold]Request received[/bold]")
    console.print(request)
    if options.dry_run:
        console.print("[yellow]Dry-run enabled - no command will be executed.[/yellow]")
    if options.autonomy:
        console.print(f"Autonomy mode requested: {options.autonomy}")
    console.print("This request is not supported by the current deterministic CLI.")


def _emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, default=str))


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
        )
    )


def _record_command_result(result: CommandResult) -> None:
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


def _dry_run_create_file(path: str, workspace_path: Path, json_output: bool) -> None:
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
    result: CommandResult, json_output: bool, *, record_audit: bool = True
) -> None:
    if record_audit:
        _record_command_result(result)
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


def _dry_run_message(command: str, effect: str, json_output: bool) -> None:
    _record_audit(
        command=None,
        exit_code=None,
        duration_seconds=0,
        final_status="dry_run",
    )
    if json_output:
        _emit_json({"status": "dry_run", "proposed_command": command, "effect": effect})
        return
    typer.echo("Dry run only — no command executed.\n")
    typer.echo(f"Proposed command:\n{command}\n")
    typer.echo(f"Effect:\n{effect}")


def _handle_deterministic_request(request: str, options: RequestOptions) -> bool:
    """Run a supported deterministic request; return false when no route matches."""
    workspace_path = Path.cwd().resolve()
    operations = DeterministicOperations(workspace_path)

    file_path = _create_file_path(request)
    if file_path is not None:
        if options.dry_run:
            _dry_run_create_file(file_path, workspace_path, options.json_output)
            return True
        started_at = monotonic()
        created_path = operations.create_file(file_path)
        _record_audit(
            command=f"create_file({file_path!r})",
            exit_code=0,
            duration_seconds=monotonic() - started_at,
            stdout=f"Created file: {_display_path(workspace_path, created_path)}",
            final_status="succeeded",
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
            )
        else:
            _emit_command_result(operations.git_status(), options.json_output)
        return True

    commit_match = re.search(r"\bcommit\b.*?\bwith\s+message\s+(.+)$", request.strip(), re.I)
    if commit_match:
        message = commit_match.group(1).strip().strip("\"'")
        if not message:
            raise ValueError("Please provide a non-empty commit message.")
        if options.dry_run:
            _dry_run_message(
                f"git add --all -- .\ngit commit -m {_powershell_literal(message)}",
                "Stages workspace changes and commits them with the supplied message.",
                options.json_output,
            )
            return True
        add_result = operations.git_add_all()
        _record_command_result(add_result)
        if not add_result.succeeded:
            _emit_command_result(add_result, options.json_output, record_audit=False)
        commit_result = operations.git_commit(message)
        _emit_command_result(commit_result, options.json_output)
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
            )
        else:
            _emit_command_result(operations.git_push(), options.json_output)
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
            try:
                handled = _handle_deterministic_request(request, options)
            except AuditLogError as error:
                typer.echo(f"Audit error: {error}", err=True)
                raise typer.Exit(code=1) from error
            except (OSError, ValueError) as error:
                try:
                    _record_audit(
                        command=request,
                        exit_code=1,
                        duration_seconds=0,
                        stderr=str(error),
                        final_status="denied" if isinstance(error, PermissionError) else "failed",
                        policy_decision="denied"
                        if isinstance(error, PermissionError)
                        else "allowed",
                    )
                except AuditLogError:
                    pass
                typer.echo(f"Error: {error}", err=True)
                raise typer.Exit(code=1) from error
            if not handled:
                _print_unsupported_request(request, options)


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


@app.command()
def history() -> None:
    """Show recent command and operation audit records."""
    try:
        records = AuditLog().recent()
    except AuditLogError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error
    if not records:
        console.print("No command history yet.")
        return

    for record in records:
        console.print(
            f"[{record.get('timestamp', 'unknown time')}] "
            f"{record.get('final_status', 'unknown')} "
            f"(policy: {record.get('policy_decision', 'unknown')}; "
            f"approval: {record.get('approval_status', 'unknown')})"
        )
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


if __name__ == "__main__":
    app()
