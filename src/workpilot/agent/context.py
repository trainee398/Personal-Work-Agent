"""Freeze the possible outbound materials before a model can request any tools."""

import heapq
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil

from workpilot.config import EXCERPT_MAX_CHARS, MAX_CONTEXT_CHARS, TOP_K_CHUNKS, Settings
from workpilot.domain.models import MemoryEntry, Project
from workpilot.errors import ExitCode, NotFoundError, PolicyError, WorkPilotError
from workpilot.ingestion.markdown import ATX_HEADING, FENCE, original_lines
from workpilot.llm.client import json_text
from workpilot.retrieval import relevance_score, tokenize
from workpilot.security.policy import SourcePolicy
from workpilot.storage.db import database, read_snapshot
from workpilot.storage.repositories import MemoryRepository, ProjectRepository, SourceRepository

MEMORY_NOTICE = "人工记忆，不是项目进度证据"
EVIDENCE_NOTICE = "文件证据仅表示在上次同步时读取到；无日期内容不得推断为当前事实。"


@dataclass(frozen=True)
class Evidence:
    relative_path: str
    line_start: int
    line_end: int
    heading: str | None
    excerpt: str = field(repr=False)
    chunk_id: str
    content_hash: str

    def public(self) -> dict:
        return {
            key: getattr(self, key)
            for key in ("relative_path", "line_start", "line_end", "heading", "excerpt")
        }


@dataclass(frozen=True)
class SourceInfo:
    relative_path: str
    sha256: str
    indexed_at: str
    size_bytes: int
    file_mtime_utc: str
    line_count: int
    headings: tuple[tuple[int, str], ...] = field(repr=False)
    stale: bool

    def public(self) -> dict:
        return {
            "relative_path": self.relative_path,
            "headings": [title for _, title in self.headings],
            "line_count": self.line_count,
            "indexed_at": self.indexed_at,
            "sha": self.sha256[:8],
            "stale": self.stale,
        }


def memory_data(entry: MemoryEntry) -> dict:
    return {"kind": entry.kind.value, "body": entry.body, "created_at": entry.created_at}


@dataclass(frozen=True)
class AskContext:
    project: Project
    question: str = field(repr=False)
    sources: tuple[SourceInfo, ...]
    evidence: tuple[Evidence, ...]
    memories: tuple[MemoryEntry, ...] = field(repr=False)
    warnings: tuple[str, ...]

    @property
    def material_chars(self) -> int:
        return len(
            json_text(
                {
                    "question": self.question,
                    "sources": [s.public() for s in self.sources],
                    "evidence": [e.public() for e in self.evidence],
                    "memories": [memory_data(m) for m in self.memories],
                }
            )
        )


def source_stale(indexed_at: str, settings: Settings) -> bool:
    try:
        timestamp = datetime.strptime(indexed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        raise WorkPilotError("来源索引时间无效，请检查本地数据库。") from None
    return (datetime.now(UTC) - timestamp).total_seconds() > settings.stale_days * 86400


def same_file_metadata(info, source) -> bool:
    mtime = datetime.fromtimestamp(info.st_mtime_ns // 1_000_000_000, UTC)
    return (
        info.st_size == source["size_bytes"]
        and mtime.strftime("%Y-%m-%dT%H:%M:%SZ") == source["file_mtime_utc"]
    )


def _headings(chunks) -> tuple[tuple[int, str], ...]:
    result = []
    fence_character, fence_length = "", 0
    for chunk in chunks:
        for number, line in enumerate(original_lines(chunk["body"]), chunk["line_start"]):
            content = line.removesuffix("\n")
            marker = FENCE.match(content)
            if fence_character:
                if (
                    marker
                    and marker[1][0] == fence_character
                    and len(marker[1]) >= fence_length
                    and not marker[2].strip()
                ):
                    fence_character = ""
                continue
            if marker and not (marker[1][0] == "`" and "`" in marker[2]):
                fence_character, fence_length = marker[1][0], len(marker[1])
                continue
            heading = ATX_HEADING.match(content)
            if heading and len(heading[1]) <= 2:
                result.append((number, (heading[2] or "").strip()))
    return tuple(result)


def prepare_context(name: str, question: str, settings: Settings) -> AskContext:
    if not question.strip():
        raise WorkPilotError("问题不能为空或仅含空白。", ExitCode.USAGE)
    if len(question) > MAX_CONTEXT_CHARS:
        raise PolicyError("问题超过上下文字符预算。")
    with database(settings.data_dir) as connection, read_snapshot(connection):
        project = ProjectRepository(connection).get(name)
        repository = SourceRepository(connection)
        policy = SourcePolicy(connection, settings)
        sources, candidates, warnings = [], [], []
        terms = set(tokenize(question))
        for relative in sorted(set(project.allowed_files)):
            cached = repository.get(project.id, relative)
            try:
                info = policy.inspect_source(project.id, relative)
            except NotFoundError:
                warnings.append(f"{relative}：来源缺失，未纳入问答材料。")
                continue
            if cached is None or not cached["active"]:
                warnings.append(f"{relative}：无可用索引，请先 sync。")
                continue
            if not same_file_metadata(info, cached):
                warnings.append(f"{relative}：索引可能过期，文件已变化；请先 sync。")
                continue
            chunks = connection.execute(
                "SELECT * FROM source_chunks WHERE source_id = ? ORDER BY line_start",
                (cached["id"],),
            ).fetchall()
            stale = source_stale(cached["indexed_at"], settings)
            sources.append(
                SourceInfo(
                    relative,
                    cached["sha256"],
                    cached["indexed_at"],
                    cached["size_bytes"],
                    cached["file_mtime_utc"],
                    max((c["line_end"] for c in chunks), default=0),
                    _headings(chunks),
                    stale,
                )
            )
            if stale:
                warnings.append(f"{relative}：索引可能过期，建议先 sync。")
            for chunk in chunks:
                score = relevance_score(terms, chunk["heading"], chunk["body"])
                if not score:
                    continue
                raw_excerpt = chunk["body"]
                clipped = len(raw_excerpt) > EXCERPT_MAX_CHARS
                if clipped:
                    raw_excerpt = raw_excerpt[: EXCERPT_MAX_CHARS - 1]
                visible_end = chunk["line_start"] + len(original_lines(raw_excerpt)) - 1
                evidence = Evidence(
                    relative,
                    chunk["line_start"],
                    min(chunk["line_end"], visible_end),
                    chunk["heading"],
                    raw_excerpt + ("…" if clipped else ""),
                    chunk["id"],
                    chunk["content_hash"],
                )
                candidates.append((score, evidence))
        chosen = heapq.nsmallest(
            TOP_K_CHUNKS,
            candidates,
            key=lambda hit: (-hit[0], hit[1].relative_path, hit[1].line_start),
        )
        context = AskContext(
            project,
            question,
            tuple(sources),
            tuple(e for _, e in chosen),
            tuple(MemoryRepository(connection).list(project.id)),
            tuple(warnings),
        )
        if context.material_chars > MAX_CONTEXT_CHARS:
            raise PolicyError("候选材料超过 12000 字符预算；请缩小授权资料或人工记忆范围。")
        return context


def assert_context_current(context: AskContext, settings: Settings) -> None:
    """Recheck before every tool and network step, including already-delivered history."""
    with database(settings.data_dir) as connection, read_snapshot(connection):
        project = ProjectRepository(connection).get_by_id(context.project.id)
        if project.root != context.project.root:
            raise PolicyError("项目根目录授权已变化，请重新提问。")
        policy = SourcePolicy(connection, settings)
        repository = SourceRepository(connection)
        for source in context.sources:
            if source.relative_path not in project.allowed_files:
                raise PolicyError("问答材料的文件授权已撤销，请重新提问。")
            row = repository.get(project.id, source.relative_path)
            if (
                row is None
                or not row["active"]
                or row["sha256"] != source.sha256
                or row["indexed_at"] != source.indexed_at
            ):
                raise WorkPilotError("问答期间来源索引已变化，请重新提问。", ExitCode.BUSY)
            info = policy.inspect_source(project.id, source.relative_path)
            if not same_file_metadata(info, row):
                raise WorkPilotError("问答期间源文件已变化，请先 sync 后重试。", ExitCode.BUSY)
        for evidence in context.evidence:
            row = connection.execute(
                "SELECT content_hash FROM source_chunks WHERE id = ?", (evidence.chunk_id,)
            ).fetchone()
            if row is None or row[0] != evidence.content_hash:
                raise WorkPilotError("问答期间证据切片已变化，请重新提问。", ExitCode.BUSY)
        current_memories = {
            entry.id: entry for entry in MemoryRepository(connection).list(project.id)
        }
        if any(current_memories.get(entry.id) != entry for entry in context.memories):
            raise WorkPilotError("问答期间人工记忆已变化，请重新提问。", ExitCode.BUSY)


def render_preview(context: AskContext) -> str:
    """Metadata-only manifest: no question, headings, excerpts or memory bodies."""
    lines = [
        f"外发预览：{context.project.name}（dry-run，未调用模型）",
        "以下是本次工具可返回的固定候选材料；实际调用可能只使用其中一部分。",
        "类型 | 来源 | 行号 | 字符数 | 估算 tokens（UTF-8 字节数 / 3，向上取整）",
    ]

    def add(kind, target, span, value):
        lines.append(
            f"{kind} | {target} | {span} | {len(value)} | {ceil(len(value.encode('utf-8')) / 3)}"
        )

    add("问题", "用户输入", "—", context.question)
    for source in context.sources:
        span = f"L{source.headings[0][0]}-L{source.headings[-1][0]}" if source.headings else "—"
        add("来源概要（标题及元数据）", source.relative_path, span, json_text(source.public()))
    for evidence in context.evidence:
        add(
            "文件切片",
            evidence.relative_path,
            f"L{evidence.line_start}-L{evidence.line_end}",
            evidence.excerpt,
        )
    for memory in context.memories:
        add(f"人工记忆/{memory.kind.value}", memory.id, "—", memory.body)
    lines.append(
        f"候选材料序列化字符数：{context.material_chars}；上下文上限：{MAX_CONTEXT_CHARS}。"
    )
    if not context.evidence:
        lines.append("无命中的可用文件证据；实际问答将返回证据不足，不调用模型。")
    lines.extend(f"告警：{warning}" for warning in context.warnings)
    return "\n".join(lines)
