"""Conservative sentence coverage plus current, delivered evidence verification."""

import re
from dataclasses import dataclass

from workpilot.agent.context import AskContext, same_file_metadata, source_stale
from workpilot.config import Settings
from workpilot.errors import WorkPilotError
from workpilot.security.policy import SourcePolicy, relative_source_path
from workpilot.storage.db import database, read_snapshot
from workpilot.storage.repositories import ProjectRepository

CITATION = re.compile(r"\[([^\[\]\r\n]+):L([0-9]+)-L([0-9]+)\]")
BRACKET = re.compile(r"\[[^\[\]\r\n]*\]")
SENTENCE_END = re.compile(r"[。！？!?；;]|(?<!\d)\.(?=\s|$)")
PUNCTUATION = " \t。.!?！？;；,，"


def chinese(question: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", question))


def insufficient(question: str) -> str:
    return (
        "现有索引不足以回答该问题。"
        if chinese(question)
        else "Insufficient indexed evidence to answer this question."
    )


@dataclass(frozen=True)
class VerifiedCitation:
    relative_path: str
    line_start: int
    line_end: int
    indexed_at: str

    @property
    def label(self) -> str:
        return f"[{self.relative_path}:L{self.line_start}-L{self.line_end}]"


@dataclass(frozen=True)
class CitationResult:
    body: str
    verified: tuple[VerifiedCitation, ...]
    invalid: tuple[str, ...]
    stale: tuple[str, ...]
    failed_closed: bool


def _covered(text: str) -> bool:
    """Each prose sentence must immediately own a citation; no free-form facts/headings."""
    statements = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("```", "{", "[{")):
            return False
        end = 0
        for match in CITATION.finditer(line):
            prefix = line[end : match.start()].strip(PUNCTUATION)
            if prefix:
                if (
                    len([part for part in SENTENCE_END.split(prefix) if part.strip(PUNCTUATION)])
                    != 1
                ):
                    return False
                statements += 1
            end = match.end()
        if line[end:].strip(PUNCTUATION):
            return False
    return statements > 0


def verify_answer(
    text: str, context: AskContext, settings: Settings, delivered: set[str]
) -> CitationResult:
    matches = list(CITATION.finditer(text))
    invalid = [
        "格式无效的引用"
        for match in BRACKET.finditer(text)
        if ":L" in match[0] and CITATION.fullmatch(match[0]) is None
    ]
    verified, stale = [], set()
    with database(settings.data_dir) as connection, read_snapshot(connection):
        try:
            project = ProjectRepository(connection).get_by_id(context.project.id)
        except WorkPilotError:
            return CitationResult(insufficient(context.question), (), ("项目登记已失效",), (), True)
        policy = SourcePolicy(connection, settings)
        for match in matches:
            path, start_text, end_text = match.groups()
            try:
                relative_source_path(path)
                if len(start_text) > 10 or len(end_text) > 10:
                    raise ValueError
                start, end = int(start_text), int(end_text)
            except (WorkPilotError, ValueError):
                invalid.append("路径或行号不安全的引用")
                continue
            eligible = [
                e
                for e in context.evidence
                if e.chunk_id in delivered
                and e.relative_path == path
                and 1 <= e.line_start <= start <= end <= e.line_end
            ]
            if (
                path not in project.allowed_files
                or project.root != context.project.root
                or not eligible
            ):
                invalid.append(match[0])
                continue
            row = connection.execute(
                """SELECT s.*, c.id AS chunk_id, c.content_hash FROM sources s
                JOIN source_chunks c ON c.source_id = s.id
                WHERE s.project_id = ? AND s.relative_path = ? AND s.active = 1
                AND c.line_start <= ? AND c.line_end >= ?""",
                (project.id, path, start, end),
            ).fetchone()
            planned = next((s for s in context.sources if s.relative_path == path), None)
            if (
                row is None
                or planned is None
                or row["sha256"] != planned.sha256
                or not any(
                    e.chunk_id == row["chunk_id"] and e.content_hash == row["content_hash"]
                    for e in eligible
                )
            ):
                invalid.append(match[0])
                continue
            try:
                info = policy.inspect_source(project.id, path)
            except WorkPilotError:
                invalid.append(match[0])
                continue
            if not same_file_metadata(info, row) or source_stale(row["indexed_at"], settings):
                stale.add(path)
            verified.append(VerifiedCitation(path, start, end, row["indexed_at"]))
    unique = tuple(sorted(set(verified), key=lambda c: (c.relative_path, c.line_start, c.line_end)))
    failed = not unique or not _covered(text)
    return CitationResult(
        insufficient(context.question) if failed else text.strip(),
        unique,
        tuple(dict.fromkeys(invalid)),
        tuple(sorted(stale)),
        failed,
    )


def render_answer(
    result: CitationResult, context: AskContext, memories_read: set[str], warnings=()
) -> str:
    zh = chinese(context.question)
    lines = []
    if result.invalid:
        lines.append("警示：存在未验证引用。" if zh else "Warning: unverified citations.")
        lines.extend(f"- {item}" for item in result.invalid)
    if result.verified and not result.failed_closed:
        lines.append(
            "文件证据（在上次同步时读取到）：" if zh else "File evidence read at the last sync:"
        )
    lines.append(result.body)
    if result.failed_closed:
        lines.append("未输出未经验证的模型原文。" if zh else "Unverified model text was withheld.")
    elif memories_read:
        lines.append(
            "人工记忆，不是项目进度证据："
            if zh
            else "Manual memories, not project progress evidence:"
        )
        for entry in context.memories:
            if entry.id in memories_read:
                lines.append(f"- {entry.kind.value} | {entry.created_at}")
                lines.extend(f"  {line}" for line in entry.body.split("\n"))
    all_warnings = list(dict.fromkeys((*context.warnings, *warnings)))
    all_warnings.extend(
        f"{path}：索引可能过期，建议先 sync。" if zh else f"{path}: index may be stale; run sync."
        for path in result.stale
    )
    lines.extend(f"告警：{warning}" if zh else f"Warning: {warning}" for warning in all_warnings)
    lines.append("引用来源：" if zh else "Citation sources:")
    if result.verified and not result.failed_closed:
        lines.extend(f"- {item.label} indexed_at={item.indexed_at}" for item in result.verified)
    else:
        lines.append("- 无已验证引用。" if zh else "- No verified citations.")
    return "\n".join(lines)
