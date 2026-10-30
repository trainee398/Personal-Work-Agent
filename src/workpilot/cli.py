"""Local project registration, read-only sync and offline diagnostics."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from workpilot.config import load_settings
from workpilot.doctor import inspect_environment
from workpilot.errors import ExitCode, WorkPilotError
from workpilot.ingestion.sync import sync_project
from workpilot.projects import ProjectRegistry
from workpilot.storage.db import SCHEMA_VERSION, database

app = typer.Typer(
    name="workpilot",
    help="WorkPilot 个人工作助手：项目登记、只读同步与离线环境检查。",
    add_completion=False,
    invoke_without_command=True,
    pretty_exceptions_show_locals=False,
)
project_app = typer.Typer(help="登记、查看或移除明确授权的项目。", no_args_is_help=True)
app.add_typer(project_app, name="project")


@contextmanager
def command_errors() -> Iterator[None]:
    try:
        yield
    except WorkPilotError as error:
        typer.echo(f"错误：{error}", err=True)
        raise typer.Exit(code=int(error.code)) from None


@app.callback()
def main(ctx: typer.Context) -> None:
    """Show help without touching configuration or the database."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command("init")
def initialize() -> None:
    """幂等初始化本地数据目录和数据库。"""
    with command_errors():
        settings = load_settings()
        with database(settings.data_dir):
            pass
        typer.echo(f"本地数据已初始化（schema_version={SCHEMA_VERSION}）。")
        typer.echo(f"数据目录：{settings.data_dir}")


@project_app.command("add")
def add_project(
    name: Annotated[str, typer.Argument(help="唯一项目名。")],
    root: Annotated[Path, typer.Option("--root", help="明确授权的本地项目根目录。")],
    files: Annotated[list[str], typer.Option("--file", help="相对 Markdown 文件名，可重复指定。")],
) -> None:
    """仅登记指定文件，检查路径元数据；不读取正文。"""
    with command_errors():
        project = ProjectRegistry(load_settings()).add(name, root, files)
        typer.echo(f"已登记项目：{project.name}")
        typer.echo(f"根目录：{project.root}")
        for relative in project.allowed_files:
            typer.echo(f"授权文件：{relative}")


@project_app.command("list")
def list_projects() -> None:
    """列出仍在登记中的项目及其授权文件。"""
    with command_errors():
        projects = ProjectRegistry(load_settings()).list()
        if not projects:
            typer.echo("暂无已登记项目。")
            return
        table = Table("项目", "根目录", "授权文件")
        for project in projects:
            table.add_row(
                Text(project.name), Text(str(project.root)), Text("\n".join(project.allowed_files))
            )
        Console(markup=False, highlight=False).print(table)


@project_app.command("remove")
def remove_project(
    name: Annotated[str, typer.Argument(help="已登记或待彻底清除的项目名。")],
    purge: Annotated[
        bool, typer.Option("--purge", help="确认后清除登记、索引及人工记忆。")
    ] = False,
) -> None:
    """普通移除保留人工记忆；彻底清除需要交互确认。"""
    with command_errors():
        registry = ProjectRegistry(load_settings())
        if not purge:
            project = registry.remove(name)
            typer.echo(f"已移除登记：{project.name}。旧索引正文已清除，人工记忆和项目名保留。")
            return
        project = registry.get(name, include_removed=True)
        try:
            confirmed = typer.confirm(
                f"永久清除项目 {project.name} 的登记、索引和人工记忆？", default=False
            )
        except typer.Abort:
            typer.echo("未收到确认，数据未清除。", err=True)
            raise typer.Exit(code=int(ExitCode.USAGE)) from None
        if not confirmed:
            typer.echo("已取消，数据未清除。")
            return
        registry.purge(name, expected_id=project.id)
        typer.echo(f"已彻底清除项目：{project.name}。")


@app.command("doctor")
def doctor() -> None:
    """完全离线检查环境和已有数据；不自动初始化或修复。"""
    with command_errors():
        report = inspect_environment()
        for check in report.checks:
            typer.echo(f"[{check.state}] {check.name}: {check.detail}")
        if report.exit_code:
            raise typer.Exit(code=report.exit_code)


@app.command("sync")
def sync(name: Annotated[str, typer.Argument(help="已登记的项目名。")]) -> None:
    """只读取授权文件，增量更新本地 Markdown 索引。"""
    with command_errors():
        report = sync_project(name, load_settings())
        typer.echo(
            f"同步结束：{report.project_name}；已索引 {report.indexed}，"
            f"未变化 {report.unchanged}，已失效 {report.invalidated}。"
        )
        for issue in report.issues:
            typer.echo(f"告警：{issue.relative_path}：{issue.message}", err=True)
        if report.exit_code:
            raise typer.Exit(code=report.exit_code)
