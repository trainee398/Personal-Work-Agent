"""Durable local audit with a closed metadata schema, never free-form log messages."""

import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from uuid import UUID

from workpilot.config import SUMMARY_MAX_CHARS, Settings
from workpilot.errors import ExitCode, NotFoundError, WorkPilotError
from workpilot.security.policy import relative_source_path
from workpilot.storage.db import database, read_snapshot, readonly_database, validate_schema
from workpilot.storage.repositories import AuditRepository, ProjectRepository

ACTIONS = frozenset(
    {
        "init",
        "project_add",
        "project_remove",
        "sync",
        "status",
        "memory_add",
        "memory_list",
        "ask",
        "audit",
        "doctor",
    }
)
TOOL_NAMES = frozenset(
    {
        "get_project_overview",
        "search_project_sources",
        "list_project_memories",
        "read_source",
        "denied_tool",
    }
)
COUNT_FIELDS = frozenset({"duration_ms", "count", "indexed", "unchanged", "invalidated"})
BOOL_FIELDS = frozenset({"dry_run", "cancelled"})
ERROR_CODES = frozenset({code.name for code in ExitCode}) | {"INTERNAL", "INTERRUPTED"}
MAX_INTEGER = 2**63 - 1
MAX_AUDIT_LIMIT = 1000


def redact_text(value: str) -> str:
    """Last-line protection for displayed metadata; raw inputs never enter audit payloads."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        value = value.replace(key, "[redacted]")
    return re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+)", "[redacted]", value)


def _label(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    value = redact_text(value)
    if (
        not value
        or len(value) > 200
        or Path(value).is_absolute()
        or PureWindowsPath(value).drive
        or ".." in Path(value).parts
        or "\\" in value
        or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value)
    ):
        return None
    return value


def _model_label(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return "[redacted]"
    value = redact_text(value)
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", value) else "[redacted]"


def _integer(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise WorkPilotError("审计计数无效；仅接受范围内的非负整数。")
    return value


def _summary(values: dict) -> str:
    """Reject unknown keys and string values instead of scrubbing arbitrary text."""
    for key, value in values.items():
        if key in COUNT_FIELDS or key == "question_chars":
            _integer(value)
        elif key in BOOL_FIELDS:
            if type(value) is not bool:
                raise WorkPilotError("审计标记无效。")
        elif key == "question_sha256":
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise WorkPilotError("审计问题指纹无效。")
        else:
            raise WorkPilotError("审计字段不在允许清单内。")
    if ("question_sha256" in values) != ("question_chars" in values):
        raise WorkPilotError("审计问题指纹与字符数必须同时存在。")
    result = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(result) > SUMMARY_MAX_CHARS:
        raise WorkPilotError("审计摘要超过长度上限。")
    return result


def _error_code(error: BaseException) -> str:
    if isinstance(error, WorkPilotError):
        try:
            return ExitCode(error.code).name
        except (ValueError, TypeError):
            return "INTERNAL"
    return "INTERNAL" if isinstance(error, Exception) else "INTERRUPTED"


class AuditError(WorkPilotError):
    """A missing durable audit stops further work without exposing storage exceptions."""


class AuditRun:
    def __init__(
        self, settings: Settings, action: str, *, question: str | None = None, dry_run: bool = False
    ) -> None:
        if action not in ACTIONS:
            raise WorkPilotError("审计动作不在允许清单内。")
        if question is not None and action != "ask":
            raise WorkPilotError("仅 ask 可记录问题指纹。")
        self.settings, self.action = settings, action
        self.id: str | None = None
        self.project_name: str | None = None
        self.model_name: str | None = None
        self.tokens_in: int | None = None
        self.tokens_out: int | None = None
        self.error_code: str | None = None
        self.values: dict = {}
        if question is not None:
            self.values.update(
                question_sha256=sha256(question.encode("utf-8")).hexdigest(),
                question_chars=len(question),
                dry_run=dry_run,
            )
        self._started = time.monotonic()

    def _write(self, operation):
        try:
            with database(self.settings.data_dir) as connection:
                return operation(AuditRepository(connection))
        except NotFoundError:
            raise
        except WorkPilotError as error:
            raise AuditError(
                "本地审计写入失败，已停止后续处理；已完成的本地操作请检查状态。", error.code
            ) from None
        except Exception:
            raise AuditError("本地审计写入失败，已停止后续处理。") from None

    def __enter__(self):
        self.id = self._write(
            lambda repository: repository.start_run(self.action, _summary(self.values))
        )
        return self

    def __exit__(self, _type, error, _traceback):
        if error is not None:
            self.error_code = _error_code(error)
        self.values["duration_ms"] = max(0, int((time.monotonic() - self._started) * 1000))
        self._save(finished=True)
        return False

    def bind_project(self, project_id: str) -> None:
        self.project_name = _label(
            self._write(lambda repository: repository.bind_project(self.id, project_id))
        )

    def bind_name(self, name: str, *, include_removed: bool = False) -> None:
        with database(self.settings.data_dir) as connection:
            project = ProjectRepository(connection).get(name, include_removed=include_removed)
        self.bind_project(project.id)

    def fail(self, code: int) -> None:
        if code:
            self.error_code = ExitCode(code).name

    def counts(self, **values: int | bool) -> None:
        if not values.keys() <= COUNT_FIELDS | BOOL_FIELDS:
            raise WorkPilotError("审计只接受指定计数和标记。")
        candidate = {**self.values, **values}
        _summary(candidate)
        self.values = candidate

    def model_started(self, model: str | None) -> None:
        if self.action != "ask":
            raise WorkPilotError("仅 ask 可记录模型用量。")
        self.model_name = _model_label(model)
        self.tokens_in, self.tokens_out = 0, 0
        self._save(finished=False)

    def usage(self, tokens_in: int, tokens_out: int) -> None:
        if self.action != "ask":
            raise WorkPilotError("仅 ask 可记录模型用量。")
        try:
            incoming = _integer((self.tokens_in or 0) + _integer(tokens_in))
            outgoing = _integer((self.tokens_out or 0) + _integer(tokens_out))
        except WorkPilotError:
            raise WorkPilotError("模型用量计数无效。", ExitCode.REMOTE) from None
        self.tokens_in, self.tokens_out = incoming, outgoing
        self._save(finished=False)

    def _save(self, *, finished: bool) -> None:
        code = self.error_code if finished else "INTERRUPTED"
        status = "denied" if code == "DENIED" else "error" if code else "success"
        summary = _summary(self.values)
        self._write(
            lambda repository: repository.update_run(
                self.id,
                status=status,
                error_code=code,
                model_name=self.model_name,
                tokens_in=self.tokens_in,
                tokens_out=self.tokens_out,
                summary=summary,
                finished=finished,
            )
        )

    def event(
        self, name: str, outcome: str, duration_ms: int = 0, *, relative_path: str | None = None
    ) -> None:
        if outcome not in {"ok", "denied", "error"}:
            raise WorkPilotError("审计工具结果无效。")
        # Model-proposed tool names and paths cannot become free-form audit strings.
        safe_name = name if name in TOOL_NAMES else "denied_tool"
        target = self.project_name
        if relative_path is not None:
            try:
                target = _label(relative_source_path(relative_path).as_posix())
            except WorkPilotError:
                target = None
        self._write(
            lambda repository: repository.add_event(
                self.id,
                safe_name,
                target,
                outcome,
                _integer(duration_ms),
            )
        )

    @contextmanager
    def tool(self, name: str, *, relative_path: str | None = None) -> Iterator[None]:
        started = time.monotonic()
        outcome = "ok"
        try:
            yield
        except BaseException as error:
            outcome = "denied" if _error_code(error) == "DENIED" else "error"
            raise
        finally:
            duration_ms = max(0, int((time.monotonic() - started) * 1000))
            self.event(name, outcome, duration_ms, relative_path=relative_path)


def _timestamp(value) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value) is not None
    )


def audit_report(settings: Settings, limit: int = 20, *, exclude_id: str | None = None) -> str:
    if type(limit) is not int or not 1 <= limit <= MAX_AUDIT_LIMIT:
        raise WorkPilotError("审计 limit 必须在 1–1000 之间。", ExitCode.USAGE)
    lines = [f"本地审计：最近 {limit} 次运行（从新到旧，工具按调用顺序）。"]
    try:
        with readonly_database(settings.data_dir) as connection, read_snapshot(connection):
            validate_schema(connection)
            repository = AuditRepository(connection)
            records = repository.list_runs(limit, exclude_id)
            for row in records:
                UUID(row["id"])
                if (
                    row["action"] not in ACTIONS
                    or row["status"] not in {"success", "error", "denied"}
                    or not _timestamp(row["started_at"])
                    or row["finished_at"] is not None
                    and not _timestamp(row["finished_at"])
                    or row["error_code"] is not None
                    and row["error_code"] not in ERROR_CODES
                ):
                    raise ValueError
                summary = json.loads(row["summary_redacted"] or "{}")
                if not isinstance(summary, dict):
                    raise ValueError
                summary_text = _summary(summary)
                for key in ("tokens_in", "tokens_out"):
                    if row[key] is not None:
                        _integer(row[key])
                target = _label(row["project_name"]) or "—（未关联或已清除）"
                lines.append(f"{row['id']} | {row['action']} | {row['status']} | 项目={target}")
                lines.append(
                    f"  {row['started_at']} → {row['finished_at'] or '未完成'} | "
                    f"错误类别={row['error_code'] or '—'} | "
                    f"模型={_model_label(row['model_name']) or '—'} | "
                    f"tokens_in={row['tokens_in'] if row['tokens_in'] is not None else '—'} | "
                    f"tokens_out={row['tokens_out'] if row['tokens_out'] is not None else '—'}"
                )
                lines.append(f"  摘要={summary_text}")
                for event in repository.list_events(row["id"]):
                    if (
                        event["tool_name"] not in TOOL_NAMES
                        or event["permission"] != "read"
                        or event["outcome"] not in {"ok", "denied", "error"}
                        or not _timestamp(event["created_at"])
                    ):
                        raise ValueError
                    duration = _integer(event["duration_ms"])
                    lines.append(
                        f"  {event['created_at']} | {event['tool_name']} | "
                        f"目标={_label(event['target_redacted']) or '—'} | read | "
                        f"{event['outcome']} | {duration} ms"
                    )
            if not records:
                lines.append("暂无审计记录。")
    except (ValueError, TypeError, KeyError):
        raise WorkPilotError("本地审计记录格式无效；未输出原始字段。") from None
    return "\n".join(lines)
