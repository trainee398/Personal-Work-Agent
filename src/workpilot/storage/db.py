"""Versioned, transactional SQLite storage with POSIX file permissions."""

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from workpilot.config import DATA_DIR_MODE, DATABASE_MODE, DATABASE_NAME
from workpilot.errors import ExitCode, WorkPilotError

SCHEMA_VERSION = 1
TABLES = (
    "projects",
    "sources",
    "source_chunks",
    "memory_entries",
    "runs",
    "tool_events",
    "meta",
)

# The two extra project columns close registration/removal gaps in the spec;
# their rationale is recorded in docs/STATUS.md. No extra business table is used.
SCHEMA_V1 = (
    """CREATE TABLE projects (
        id TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL UNIQUE,
        root_path TEXT NOT NULL,
        allowed_files_json TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE sources (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(id),
        relative_path TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'markdown' CHECK(kind IN ('markdown')),
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
        size_bytes INTEGER CHECK(size_bytes >= 0),
        file_mtime_utc TEXT,
        indexed_at TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
        UNIQUE(project_id, relative_path)
    )""",
    """CREATE TABLE source_chunks (
        id TEXT PRIMARY KEY NOT NULL,
        source_id TEXT NOT NULL REFERENCES sources(id),
        heading TEXT,
        line_start INTEGER NOT NULL CHECK(line_start >= 1),
        line_end INTEGER NOT NULL CHECK(line_end >= line_start),
        body TEXT NOT NULL,
        content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
        UNIQUE(source_id, line_start, line_end, content_hash)
    )""",
    """CREATE TABLE memory_entries (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(id),
        kind TEXT NOT NULL CHECK(kind IN ('decision', 'note', 'preference')),
        body TEXT NOT NULL,
        origin TEXT NOT NULL DEFAULT 'manual' CHECK(origin IN ('manual')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT REFERENCES projects(id),
        action TEXT NOT NULL CHECK(action IN (
            'init', 'project_add', 'project_remove', 'sync', 'status',
            'memory_add', 'memory_list', 'ask', 'audit', 'doctor'
        )),
        status TEXT NOT NULL CHECK(status IN ('success', 'error', 'denied')),
        started_at TEXT NOT NULL,
        finished_at TEXT,
        model_name TEXT,
        tokens_in INTEGER CHECK(tokens_in >= 0),
        tokens_out INTEGER CHECK(tokens_out >= 0),
        error_code TEXT,
        summary_redacted TEXT
    )""",
    """CREATE TABLE tool_events (
        id TEXT PRIMARY KEY NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(id),
        tool_name TEXT NOT NULL,
        target_redacted TEXT,
        permission TEXT NOT NULL CHECK(permission IN ('read')),
        outcome TEXT NOT NULL CHECK(outcome IN ('ok', 'denied', 'error')),
        duration_ms INTEGER CHECK(duration_ms >= 0),
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE meta (
        schema_version INTEGER NOT NULL CHECK(schema_version >= 1)
    )""",
    "CREATE INDEX idx_sources_project_active ON sources(project_id, active)",
    "CREATE INDEX idx_source_chunks_source ON source_chunks(source_id)",
    "CREATE INDEX idx_runs_started_at ON runs(started_at)",
    "CREATE INDEX idx_memories_project_created ON memory_entries(project_id, created_at)",
)

REQUIRED_COLUMNS = {
    "projects": {
        "id",
        "name",
        "root_path",
        "allowed_files_json",
        "active",
        "created_at",
        "updated_at",
    },
    "sources": {
        "id",
        "project_id",
        "relative_path",
        "kind",
        "sha256",
        "size_bytes",
        "file_mtime_utc",
        "indexed_at",
        "active",
    },
    "source_chunks": {
        "id",
        "source_id",
        "heading",
        "line_start",
        "line_end",
        "body",
        "content_hash",
    },
    "memory_entries": {"id", "project_id", "kind", "body", "origin", "created_at", "updated_at"},
    "runs": {
        "id",
        "project_id",
        "action",
        "status",
        "started_at",
        "finished_at",
        "model_name",
        "tokens_in",
        "tokens_out",
        "error_code",
        "summary_redacted",
    },
    "tool_events": {
        "id",
        "run_id",
        "tool_name",
        "target_redacted",
        "permission",
        "outcome",
        "duration_ms",
        "created_at",
    },
    "meta": {"schema_version"},
}


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def schema_version(connection: sqlite3.Connection) -> int:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not tables:
        return 0
    if "meta" not in tables:
        raise WorkPilotError("数据库缺少 WorkPilot 版本信息，不会覆盖现有数据。")
    rows = connection.execute("SELECT schema_version FROM meta").fetchall()
    if len(rows) != 1 or not isinstance(rows[0][0], int) or rows[0][0] < 1:
        raise WorkPilotError("数据库版本记录无效，请检查数据目录。")
    return rows[0][0]


def validate_schema(connection: sqlite3.Connection) -> None:
    if schema_version(connection) != SCHEMA_VERSION:
        raise WorkPilotError("数据库版本不受当前程序支持，请勿覆盖或降级现有数据库。")
    for table, expected in REQUIRED_COLUMNS.items():
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if not expected <= actual:
            raise WorkPilotError("数据库结构不完整，请检查数据目录。")


def migrate(connection: sqlite3.Connection) -> None:
    """Upgrade an empty database to v1 atomically; repeat calls preserve data."""
    version = schema_version(connection)
    if version == 0:
        with transaction(connection):
            if schema_version(connection) == 0:
                for statement in SCHEMA_V1:
                    connection.execute(statement)
                connection.execute("INSERT INTO meta(schema_version) VALUES (?)", (SCHEMA_VERSION,))
    validate_schema(connection)


def _verify_regular_file(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise WorkPilotError("数据库及附属文件必须为独立的普通文件。")
    if info.st_uid != os.geteuid():
        raise WorkPilotError("运行数据必须属于当前用户。")


def _prepare_file(path: Path, *, create: bool) -> None:
    created = False
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        if create:
            try:
                descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, DATABASE_MODE)
                created = True
            except FileExistsError:
                descriptor = os.open(path, flags)
        else:
            descriptor = os.open(path, flags)
    except FileNotFoundError:
        if not create:
            return
        raise
    try:
        info = os.fstat(descriptor)
        _verify_regular_file(info)
        if not created and not info.st_mode & stat.S_IWUSR:
            raise WorkPilotError("数据库不可写，请检查文件权限。")
        os.fchmod(descriptor, DATABASE_MODE)
    finally:
        os.close(descriptor)


def prepare_storage(data_dir: Path) -> Path:
    if data_dir in {Path(data_dir.anchor), Path.home()}:
        raise WorkPilotError("请为 WorkPilot 指定独立的数据子目录。")
    created = False
    try:
        data_dir.mkdir(mode=DATA_DIR_MODE, parents=True)
        created = True
    except FileExistsError:
        pass
    descriptor = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid():
            raise WorkPilotError("运行数据目录必须属于当前用户。")
        if not created and not info.st_mode & stat.S_IWUSR:
            raise WorkPilotError("数据目录不可写，请检查目录权限。")
        os.fchmod(descriptor, DATA_DIR_MODE)
    finally:
        os.close(descriptor)
    database_path = data_dir / DATABASE_NAME
    _prepare_file(database_path, create=True)
    for suffix in ("-wal", "-shm", "-journal"):
        _prepare_file(data_dir / (DATABASE_NAME + suffix), create=False)
    return database_path


def storage_error(error: OSError | sqlite3.Error) -> WorkPilotError:
    code = getattr(error, "sqlite_errorcode", 0) & 0xFF
    if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return WorkPilotError("本地数据库正忙，请稍后重试。", ExitCode.BUSY)
    return WorkPilotError("无法访问本地数据库，请检查数据目录、权限及数据库完整性。")


@contextmanager
def database(data_dir: Path) -> Iterator[sqlite3.Connection]:
    connection = None
    try:
        database_path = prepare_storage(data_dir)
        connection = sqlite3.connect(database_path, timeout=0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        version = schema_version(connection)
        if version not in {0, SCHEMA_VERSION}:
            raise WorkPilotError("数据库版本不受当前程序支持，请勿覆盖或降级现有数据库。")
        if version:
            validate_schema(connection)
        mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if mode != "wal":
            raise WorkPilotError("数据库无法启用 WAL。")
        migrate(connection)
        yield connection
    except (OSError, sqlite3.Error) as error:
        raise storage_error(error) from None
    finally:
        if connection is not None:
            connection.close()


@contextmanager
def readonly_database(data_dir: Path) -> Iterator[sqlite3.Connection]:
    """Inspect an existing database without initialization, repair or migration."""
    connection = None
    try:
        if data_dir.is_symlink() or not data_dir.is_dir():
            raise WorkPilotError("数据目录不存在或不是普通目录，请先运行 workpilot init。")
        path = data_dir / DATABASE_NAME
        _verify_regular_file(path.lstat())
        for suffix in ("-wal", "-shm", "-journal"):
            sibling = data_dir / (DATABASE_NAME + suffix)
            if sibling.exists() or sibling.is_symlink():
                _verify_regular_file(sibling.lstat())
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
    except (OSError, sqlite3.Error) as error:
        raise storage_error(error) from None
    finally:
        if connection is not None:
            connection.close()
