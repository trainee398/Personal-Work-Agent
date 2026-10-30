"""The T00 CLI scaffold; business commands are added in later tasks."""

import typer

app = typer.Typer(
    name="workpilot",
    help="WorkPilot 个人工作助手。T00 仅提供项目骨架与帮助。",
    add_completion=False,
    invoke_without_command=True,
)


@app.callback()
def main(ctx: typer.Context) -> None:
    """Display help until a business subcommand is implemented."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
