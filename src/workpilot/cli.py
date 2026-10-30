"""Local registration, indexing, offline status, manual memories and diagnostics."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from workpilot.agent.context import prepare_context, render_preview
from workpilot.agent.runtime import ask_question
from workpilot.config import load_settings
from workpilot.doctor import inspect_environment
from workpilot.domain.models import MemoryKind
from workpilot.errors import ExitCode, WorkPilotError
from workpilot.ingestion.sync import sync_project
from workpilot.memory import MemoryStore
from workpilot.projects import ProjectRegistry
from workpilot.status import project_status, render_status
from workpilot.storage.db import SCHEMA_VERSION, database

app = typer.Typer(
    name="workpilot",
    help="WorkPilot 个人工作助手：只读项目索引、离线状态、人工记忆与有来源的问答。",
    add_completion=False,
    invoke_without_command=True,
    pretty_exceptions_show_locals=False,
)
project_app = typer.Typer(help="登记、查看或移除明确授权的项目。", no_args_is_help=True)
app.add_typer(project_app, name="project")
memory_app = typer.Typer(help="显式记录或查看人工记忆；不是项目进度证据。", no_args_is_help=True)
app.add_typer(memory_app, name="memory")


def literal_text(value: str) -> str:
    """Show untrusted text literally without terminal control sequences."""
    return "".join(
        f"\\x{ord(character):02x}"
        if (ord(character) < 32 and character not in "\n\t") or 127 <= ord(character) < 160
        else character
        for character in value
    )


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


@app.command("status")
def status(name: Annotated[str, typer.Argument(help="已登记的项目名。")]) -> None:
    """离线展示已索引来源、摘录与新鲜度；不读取源文件正文。"""
    with command_errors():
        report = project_status(name, load_settings())
        typer.echo(literal_text(render_status(report)))


@memory_app.command("add")
def add_memory(
    name: Annotated[str, typer.Argument(help="已登记的项目名。")],
    kind: Annotated[MemoryKind, typer.Option("--kind", help="人工决策、备注或偏好。")],
    text: Annotated[str, typer.Option("--text", help="明确输入的记忆正文，不能为空。")],
) -> None:
    """保存一条人工记忆；不写入源项目文件或文件证据索引。"""
    with command_errors():
        entry = MemoryStore(load_settings()).add(name, kind, text)
        typer.echo(f"已保存人工记忆：{entry.id}（{entry.kind.value}，origin={entry.origin}）。")
        typer.echo(f"创建时间：{entry.created_at}")
        typer.echo("人工记忆，不是项目进度证据。")


@memory_app.command("list")
def list_memories(
    name: Annotated[str, typer.Argument(help="已登记的项目名。")],
    kind: Annotated[MemoryKind | None, typer.Option("--kind", help="仅列出指定类别。")] = None,
) -> None:
    """按创建时间、ID 升序列出人工记忆，可按类别筛选。"""
    with command_errors():
        entries = MemoryStore(load_settings()).list(name, kind)
        typer.echo(literal_text(f"项目：{name}；人工记忆，不是项目进度证据。"))
        if not entries:
            typer.echo("暂无符合条件的人工记忆。")
        for entry in entries:
            typer.echo(f"{entry.id} | kind={entry.kind.value} | origin={entry.origin}")
            typer.echo(f"创建时间：{entry.created_at}；更新时间：{entry.updated_at}")
            typer.echo("正文：")
            for line in literal_text(entry.body).split("\n"):
                typer.echo(f"  {line}")


@app.command("ask")
def ask(
    name: Annotated[str, typer.Argument(help="已登记的项目名。")],
    question: Annotated[str, typer.Argument(help="基于项目证据的问题。")],
    allow_remote: Annotated[
        bool, typer.Option("--allow-remote", help="允许本次选定材料发送至官方 OpenAI API。")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="仅预览外发范围，零网络、无需 API Key。")
    ] = False,
) -> None:
    """用三个只读工具回答问题；未授权、证据不足或引用无效时失败关闭。"""
    with command_errors():
        settings = load_settings()
        if dry_run:
            text = render_preview(prepare_context(name, question, settings))
        else:
            text = ask_question(name, question, settings, allow_remote=allow_remote).text
        typer.echo(literal_text(text))
