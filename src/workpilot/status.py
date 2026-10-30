"""Deterministic offline reports from a consistent, authorized index snapshot."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from workpilot.config import EXCERPT_MAX_CHARS, Settings
from workpilot.domain.models import Project
from workpilot.errors import NotFoundError, WorkPilotError
from workpilot.security.policy import SourcePolicy
from workpilot.storage.db import database, read_snapshot
from workpilot.storage.repositories import ProjectRepository, SourceRepository


@dataclass(frozen=True)
class StatusExcerpt:
    relative_path: str
    line_start: int
    line_end: int
    body: str = field(repr=False)
    indexed_at: str


@dataclass(frozen=True)
class SourceStatus:
    relative_path: str
    state: Literal["active", "missing", "inactive", "unindexed"]
    sha_prefix: str | None
    line_count: int | None
    indexed_at: str | None
    stale: bool = False


@dataclass(frozen=True)
class ProjectStatus:
    project: Project
    last_sync: str | None
    sources: tuple[SourceStatus, ...]
    warnings: tuple[str, ...]
    excerpts: tuple[StatusExcerpt, ...]


def _timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        raise WorkPilotError("来源索引时间无效，请检查本地数据库。") from None


def project_status(name: str, settings: Settings, *, now: datetime | None = None) -> ProjectStatus:
    """Never sync, read source bodies, infer progress, or fall back to personal notes."""
    current_time = datetime.now(UTC) if now is None else now
    with database(settings.data_dir) as connection, read_snapshot(connection):
        project = ProjectRepository(connection).get(name)
        repository = SourceRepository(connection)
        policy = SourcePolicy(connection, settings)
        metadata = {row["relative_path"]: row for row in repository.list_metadata(project.id)}
        times = [row["indexed_at"] for row in metadata.values()]
        last_sync = max(times, key=_timestamp) if times else None
        sources = []
        warnings = []
        excerpts = []
        indexed_chunks = 0
        for relative in sorted(set(project.allowed_files)):
            cached = metadata.get(relative)
            active = cached is not None and bool(cached["active"])
            stale = False
            changed = False
            state = "active" if active else "inactive" if cached is not None else "unindexed"
            if active:
                indexed_chunks += cached["chunk_count"]
            try:
                info = policy.inspect_source(project.id, relative)
            except NotFoundError:
                state = "missing"
                warnings.append(f"{relative}：来源缺失；请先恢复文件或更新授权，再 sync。")
            else:
                if cached is None:
                    warnings.append(f"{relative}：尚未索引，请先 sync。")
                elif not active:
                    warnings.append(f"{relative}：来源索引已失效，请先 sync。")
                else:
                    mtime = datetime.fromtimestamp(info.st_mtime_ns // 1_000_000_000, UTC)
                    changed = cached["size_bytes"] != info.st_size or cached[
                        "file_mtime_utc"
                    ] != mtime.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if changed:
                        warnings.append(
                            f"{relative}：文件元数据已变化，索引可能过期；"
                            "旧摘录已隐藏，建议先 sync。"
                        )
            if active:
                age_seconds = (current_time - _timestamp(cached["indexed_at"])).total_seconds()
                expired = age_seconds > settings.stale_days * 86400
                stale = changed or expired
                if expired:
                    warnings.append(
                        f"{relative}：索引可能过期（超过 {settings.stale_days} 天），建议先 sync。"
                    )
                if state == "active" and not changed:
                    chunk = repository.first_chunk(project.id, relative)
                    if chunk is None:
                        warnings.append(f"{relative}：索引为空。")
                    else:
                        body = chunk["body"]
                        if len(body) > EXCERPT_MAX_CHARS:
                            body = body[: EXCERPT_MAX_CHARS - 1] + "…"
                        excerpts.append(
                            StatusExcerpt(
                                relative,
                                chunk["line_start"],
                                chunk["line_end"],
                                body,
                                cached["indexed_at"],
                            )
                        )
            sources.append(
                SourceStatus(
                    relative_path=relative,
                    state=state,
                    sha_prefix=cached["sha256"][:8] if cached is not None else None,
                    line_count=cached["line_count"] if cached is not None else None,
                    indexed_at=cached["indexed_at"] if cached is not None else None,
                    stale=stale,
                )
            )
        if metadata.keys() - set(project.allowed_files):
            warnings.append("存在已撤销授权的来源；旧正文不会展示，建议先 sync 清理。")
        if not indexed_chunks:
            warnings.append("无已索引资料。")
        elif not excerpts:
            warnings.append("无可展示的文件证据；请先 sync。")
        return ProjectStatus(project, last_sync, tuple(sources), tuple(warnings), tuple(excerpts))


def render_status(report: ProjectStatus) -> str:
    """Fixed report order and plain text, independent of terminal width or model output."""
    lines = [f"项目：{report.project.name}", f"根目录：{report.project.root}"]
    lines.append(
        "上次同步时间（已记录来源 indexed_at 的最大值）："
        + (report.last_sync or "尚无已记录的来源同步时间")
    )
    lines.extend(
        [
            "来源（按相对路径排序；行数来自已存切片）：",
            "相对路径 | 状态 | SHA-256 前 8 位 | 索引行数 | indexed_at | 过期标记",
        ]
    )
    for source in report.sources:
        lines.append(
            " | ".join(
                [
                    source.relative_path,
                    source.state,
                    source.sha_prefix or "—",
                    str(source.line_count) if source.line_count is not None else "—",
                    source.indexed_at or "—",
                    "索引可能过期" if source.stale else "—",
                ]
            )
        )
    lines.append("告警：")
    lines.extend(f"- {warning}" for warning in report.warnings)
    if not report.warnings:
        lines.append("- 无。")
    if report.excerpts:
        lines.append("文件证据摘录（在上次同步时读取到；不据此推断当前进度）：")
        for excerpt in report.excerpts:
            lines.append(
                f"[{excerpt.relative_path}:L{excerpt.line_start}-L{excerpt.line_end}] "
                f"indexed_at={excerpt.indexed_at}"
            )
            lines.extend(f"  {line}" for line in excerpt.body.split("\n"))
    return "\n".join(lines)
