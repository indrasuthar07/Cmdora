"""The Cmdora command-line entry point."""

from typing import Annotated

import typer
from rich.console import Console

from cmdora import __version__

app = typer.Typer(
    name="Cmdora",
    help="A secure natural-language PowerShell and Git assistant.",
    no_args_is_help=True,
    invoke_without_command=True,
    add_completion=False,
)
console = Console()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the installed Cmdora version and exit."),
    ] = False,
) -> None:
    """Plan and run approved PowerShell and Git workflows."""
    if version:
        console.print(f"Cmdora {__version__}")
        raise typer.Exit()


if __name__ == "__main__":
    app()
