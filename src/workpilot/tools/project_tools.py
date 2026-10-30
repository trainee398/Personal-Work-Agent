"""Validate tool inputs and read only the material scope frozen for this question."""

import json
from collections.abc import Callable
from contextlib import nullcontext

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from workpilot.agent.context import (
    EVIDENCE_NOTICE,
    MEMORY_NOTICE,
    AskContext,
    assert_context_current,
    memory_data,
)
from workpilot.config import MAX_CONTEXT_CHARS, TOP_K_CHUNKS, Settings
from workpilot.domain.models import MemoryKind
from workpilot.errors import PolicyError
from workpilot.llm.client import ToolDefinition
from workpilot.retrieval import relevance_score, tokenize
from workpilot.security.audit import AuditRun


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str = Field(min_length=1, max_length=36)


class SearchInput(ProjectInput):
    query: str = Field(max_length=MAX_CONTEXT_CHARS)


class MemoryInput(ProjectInput):
    kind: MemoryKind | None = None


INPUT_TYPES = {
    "get_project_overview": ProjectInput,
    "search_project_sources": SearchInput,
    "list_project_memories": MemoryInput,
}
DESCRIPTIONS = {
    "get_project_overview": "查看本次已授权来源的一二级标题、索引时间与新鲜度；不代表实时进度。",
    "search_project_sources": "在本次已预览的文件切片内按关键词检索，返回至多 8 条有行号的证据。",
    "list_project_memories": "查看显式人工记忆，可按 kind 筛选；人工记忆不是项目进度证据。",
}


def tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(
        ToolDefinition(name, DESCRIPTIONS[name], schema.model_json_schema())
        for name, schema in INPUT_TYPES.items()
    )


class ProjectTools:
    def __init__(
        self, context: AskContext, settings: Settings, audit: AuditRun | None = None
    ) -> None:
        self.context, self.settings = context, settings
        self.audit = audit
        self.delivered: set[str] = set()
        self.memories_read: set[str] = set()

    def invoke(
        self, name: str, arguments: str, *, check_result: Callable[[dict], None] | None = None
    ) -> dict:
        scope = (
            nullcontext(self.audit)
            if self.audit is not None
            else AuditRun(self.settings, "ask", question=self.context.question)
        )
        before_evidence, before_memories = self.delivered.copy(), self.memories_read.copy()
        try:
            with scope as audit, audit.tool(name):
                if self.audit is None:
                    audit.bind_project(self.context.project.id)
                result = self._invoke(name, arguments)
                if check_result is not None:
                    check_result(result)
                return result
        except BaseException:
            self.delivered, self.memories_read = before_evidence, before_memories
            raise

    def _invoke(self, name: str, arguments: str) -> dict:
        if name not in INPUT_TYPES:
            raise PolicyError("请求的工具不在三个只读工具的允许清单内。")
        if len(arguments) > MAX_CONTEXT_CHARS:
            raise PolicyError("工具参数超过字符预算。")
        try:
            parsed = INPUT_TYPES[name].model_validate_json(arguments)
        except (ValidationError, ValueError, json.JSONDecodeError):
            raise PolicyError("只读工具参数无效，不接受额外字段或文件路径。") from None
        if parsed.project_id != self.context.project.id:
            raise PolicyError("只读工具只能访问本次提问的已登记项目。")
        assert_context_current(self.context, self.settings)
        if name == "get_project_overview":
            return {
                "sources": [source.public() for source in self.context.sources],
                "notice": EVIDENCE_NOTICE,
            }
        if name == "list_project_memories":
            entries = [
                m for m in self.context.memories if parsed.kind is None or m.kind == parsed.kind
            ]
            self.memories_read.update(m.id for m in entries)
            return {"memories": [memory_data(entry) for entry in entries], "notice": MEMORY_NOTICE}
        terms = set(tokenize(parsed.query))
        ranked = [(relevance_score(terms, e.heading, e.excerpt), e) for e in self.context.evidence]
        ranked.sort(key=lambda pair: (-pair[0], pair[1].relative_path, pair[1].line_start))
        hits = [e for score, e in ranked if score][:TOP_K_CHUNKS]
        self.delivered.update(e.chunk_id for e in hits)
        return {"chunks": [e.public() for e in hits], "notice": EVIDENCE_NOTICE}
