"""Offline diagnostics without implicit database creation, migration or repair."""

import fcntl
import importlib.util
import os
import sqlite3
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from workpilot.config import (
    DATA_DIR_MODE,
    DATABASE_MODE,
    DEFAULT_PARAMETERS,
    load_settings,
)
from workpilot.errors import ExitCode, WorkPilotError
from workpilot.storage.db import TABLES, readonly_database, schema_version, validate_schema
from workpilot.storage.repositories import ProjectRepository


@dataclass(frozen=True)
class Check:
    name: str
    state: Literal["OK", "WARN", "ERROR"]
    detail: str


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)
    exit_code: int = 0

    def add(self, name: str, healthy: bool, detail: str) -> None:
        self.checks.append(Check(name, "OK" if healthy else "ERROR", detail))
        if not healthy:
            self.exit_code = int(ExitCode.CONFIGURATION)


def _fts5_available() -> bool:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(content)")
        return True
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def _lock_state(path: Path) -> Check:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return Check("lock", "OK", "未创建（当前没有锁文件）")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return Check("lock", "ERROR", "锁路径不是独立普通文件")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return Check("lock", "WARN", "已占用；检查未等待")
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return Check("lock", "OK", "空闲")
    finally:
        os.close(descriptor)


def inspect_environment() -> DoctorReport:
    report = DoctorReport()
    report.add("Python", sys.version_info >= (3, 11), sys.version.split()[0])
    report.add("SQLite", True, sqlite3.sqlite_version)
    fts5 = _fts5_available()
    report.checks.append(
        Check("FTS5", "OK" if fts5 else "WARN", "可用" if fts5 else "不可用（可选）")
    )
    tracing_disabled = os.environ.get("OPENAI_AGENTS_DISABLE_TRACING") == "1"
    report.add(
        "tracing", tracing_disabled, "环境变量已关闭" if tracing_disabled else "环境变量未关闭"
    )
    sdk_installed = importlib.util.find_spec("agents") is not None
    if sdk_installed and tracing_disabled:
        from workpilot.llm.openai_client import tracing_status

        try:
            sdk_version, disabled = tracing_status()
            report.add(
                "Agents SDK",
                disabled,
                f"{sdk_version}；SDK 运行时 tracing 已关闭（NoOpTrace）"
                if disabled
                else "SDK 运行时 tracing 未关闭",
            )
        except WorkPilotError as error:
            report.add("Agents SDK", False, str(error))
    else:
        report.checks.append(
            Check("Agents SDK", "WARN", "未安装或 tracing 环境变量异常；未导入模型 SDK")
        )
    try:
        settings = load_settings()
    except WorkPilotError as error:
        report.add("configuration", False, str(error))
        return report
    report.add("configuration", True, "解析成功；不加载 .env")
    report.add(
        "OPENAI_API_KEY", True, "已设置" if settings.api_key_set else "未设置（本地命令可用）"
    )
    report.add("WORKPILOT_MODEL", True, settings.model or "未设置")
    report.add("WORKPILOT_SEND_TO_MODEL", True, str(settings.send_to_model).lower())
    report.add("WORKPILOT_STALE_DAYS", True, str(settings.stale_days))
    report.add("WORKPILOT_DATA_DIR", True, str(settings.data_dir))

    try:
        info = settings.data_dir.lstat()
        directory_ok = stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
        report.add(
            "data directory",
            directory_ok,
            "当前用户的普通目录" if directory_ok else "目录类型或属主无效",
        )
        if not directory_ok:
            return report
        report.add(
            "directory permissions",
            stat.S_IMODE(info.st_mode) == DATA_DIR_MODE,
            f"{stat.S_IMODE(info.st_mode):04o}",
        )
        writable = bool(info.st_mode & stat.S_IWUSR) and os.access(
            settings.data_dir, os.R_OK | os.W_OK | os.X_OK
        )
        report.add("directory writable", writable, "可写" if writable else "不可写")
    except OSError:
        report.add("data directory", False, "目录不存在或不可访问；请先运行 workpilot init")
        return report

    try:
        info = settings.database_path.lstat()
        valid_file = (
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
        )
        report.add(
            "database file",
            valid_file,
            "当前用户的独立普通文件" if valid_file else "数据库文件类型或属主无效",
        )
        if not valid_file:
            return report
        report.add(
            "database permissions",
            stat.S_IMODE(info.st_mode) == DATABASE_MODE,
            f"{stat.S_IMODE(info.st_mode):04o}",
        )
        report.add(
            "database writable",
            bool(info.st_mode & stat.S_IWUSR) and os.access(settings.database_path, os.W_OK),
            "检查数据库写入权限",
        )
        with readonly_database(settings.data_dir) as connection:
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            report.add(
                "integrity_check",
                integrity == ["ok"],
                "通过" if integrity == ["ok"] else "数据库完整性检查失败",
            )
            validate_schema(connection)
            report.add("schema_version", True, str(schema_version(connection)))
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            report.add("journal_mode", mode == "wal", mode)
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            report.add(
                "foreign_keys", not violations, "通过" if not violations else "存在外键约束错误"
            )
            ProjectRepository(connection).list(include_removed=True)
            report.add("project configuration", True, "登记数据可解析")
            for table in TABLES:
                count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                report.add(f"count.{table}", True, str(count))
    except OSError:
        report.add("database", False, "数据库不存在或不可访问；请先运行 workpilot init")
    except sqlite3.Error:
        report.add("database", False, "数据库检查失败，请检查完整性与锁状态")
    except WorkPilotError as error:
        report.add("database", False, str(error))
        if error.code == ExitCode.BUSY:
            report.exit_code = int(ExitCode.BUSY)

    try:
        lock = _lock_state(settings.lock_path)
        report.checks.append(lock)
        if lock.state == "ERROR":
            report.exit_code = int(ExitCode.CONFIGURATION)
    except OSError:
        report.add("lock", False, "锁文件不可安全访问")
    for name, value in DEFAULT_PARAMETERS.items():
        detail = ", ".join(value) if isinstance(value, tuple) else str(value)
        report.add(f"default.{name}", True, detail)
    return report
