"""The Cmdora command-line entry point."""

import json
from dataclasses import asdict, dataclass, replace
from typing import Annotated, Any

import typer
from rich.console import Console
from typer.core import TyperGroup

from cmdora import __version__


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


def _print_request_received(request: str, options: RequestOptions) -> None:
    """Show that a request was parsed without attempting execution yet."""
    payload = {
        "status": "not_implemented",
        "request": request,
        "options": asdict(options),
        "message": "Request planning and execution will be added in later Phase 1 tasks.",
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
    console.print("Planning and execution will be added in later Phase 1 tasks.")


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
            _print_request_received(request, options)


@config_app.command("init")
def config_init() -> None:
    """Initialize repository-specific configuration in a later phase."""
    console.print("Configuration initialization will be available in Phase 2.")


@app.command()
def history() -> None:
    """Show audit history after audit storage is added in Phase 2."""
    console.print("History will be available after audit storage is added in Phase 2.")


if __name__ == "__main__":
    app()
