"""Exercise real SQLite constraints, migrations and file permissions."""

import sqlite3
import stat
from pathlib import Path

import pytest

from workpilot.config import load_settings
from workpilot.errors import ExitCode, WorkPilotError
from workpilot.projects import ProjectRegistry
from workpilot.storage import db


def test_initialization_and_migration_are_repeatable(
    isolated_environment: Path, demo_project: Path
) -> None:
    settings = load_settings()
    with db.database(isolated_environment) as connection:
        db.migrate(connection)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    original = ProjectRegistry(settings).add("demo", demo_project, ["STATUS.md"])

    with db.database(isolated_environment) as connection:
        db.migrate(connection)
        db.migrate(connection)
        assert db.schema_version(connection) == 1
        assert connection.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert tables == {
            "projects",
            "sources",
            "source_chunks",
            "memory_entries",
            "runs",
            "tool_events",
            "meta",
        }
        indices = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert {
            "idx_sources_project_active",
            "idx_source_chunks_source",
            "idx_runs_started_at",
            "idx_memories_project_created",
        } <= indices
    assert ProjectRegistry(settings).get("demo") == original


def test_database_and_sidecars_are_private(isolated_environment: Path) -> None:
    with db.database(isolated_environment) as connection:
        connection.execute("SELECT * FROM projects").fetchall()
        assert stat.S_IMODE(isolated_environment.stat().st_mode) == 0o700
        for name in ("workpilot.db", "workpilot.db-wal", "workpilot.db-shm"):
            path = isolated_environment / name
            if path.exists():
                assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("table", "values"),
    [
        ("projects", {"active": 2}),
        ("sources", {"active": 2}),
        ("sources", {"kind": "shell"}),
        ("source_chunks", {"line_start": 0}),
        ("source_chunks", {"line_end": 0}),
        ("memory_entries", {"kind": "automatic-fact"}),
        ("memory_entries", {"origin": "model"}),
        ("runs", {"action": "shell"}),
        ("runs", {"status": "unknown"}),
        ("runs", {"tokens_in": -1}),
        ("tool_events", {"permission": "write_local"}),
        ("tool_events", {"outcome": "unknown"}),
        ("tool_events", {"duration_ms": -1}),
    ],
)
def test_schema_rejects_invalid_values(
    table: str, values: dict[str, object], isolated_environment: Path, demo_project: Path
) -> None:
    project = ProjectRegistry(load_settings()).add("demo", demo_project, ["STATUS.md"])
    timestamp = project.created_at
    with db.database(isolated_environment) as connection:
        connection.execute(
            """INSERT INTO sources(id, project_id, relative_path, sha256, indexed_at)
            VALUES ('source', ?, 'STATUS.md', ?, ?)""",
            (project.id, "a" * 64, timestamp),
        )
        connection.execute(
            """INSERT INTO source_chunks(id, source_id, line_start, line_end, body, content_hash)
            VALUES ('chunk', 'source', 1, 1, '纯虚构正文', ?)""",
            ("b" * 64,),
        )
        connection.execute(
            """INSERT INTO memory_entries(id, project_id, kind, body, created_at, updated_at)
            VALUES ('memory', ?, 'note', '虚构人工备注', ?, ?)""",
            (project.id, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO runs(id, project_id, action, status, started_at)
            VALUES ('run', ?, 'init', 'success', ?)""",
            (project.id, timestamp),
        )
        connection.execute(
            """INSERT INTO tool_events(id, run_id, tool_name, permission, outcome, created_at)
            VALUES ('event', 'run', 'fictional_tool', 'read', 'ok', ?)""",
            (timestamp,),
        )
        field, value = next(iter(values.items()))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(f"UPDATE {table} SET {field} = ?", (value,))


def test_foreign_keys_reject_orphaned_records(isolated_environment: Path) -> None:
    with db.database(isolated_environment) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO memory_entries(id, project_id, kind, body, created_at, updated_at)
                VALUES ('orphan', 'missing', 'note', '虚构备注', 'now', 'now')"""
            )


def test_transaction_failure_rolls_back_changes(isolated_environment: Path) -> None:
    with db.database(isolated_environment) as connection:
        with pytest.raises(RuntimeError), db.transaction(connection):
            connection.execute("UPDATE meta SET schema_version = 2")
            raise RuntimeError("fictional failure")
        assert db.schema_version(connection) == 1


def test_failed_migration_leaves_no_partial_tables(
    isolated_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(db, "SCHEMA_V1", (*db.SCHEMA_V1, "INVALID SQL"))
        with pytest.raises(WorkPilotError):
            with db.database(isolated_environment):
                pass
    connection = sqlite3.connect(isolated_environment / "workpilot.db")
    try:
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        )
    finally:
        connection.close()
    with db.database(isolated_environment) as connection:
        assert db.schema_version(connection) == 1


def test_future_schema_is_not_downgraded(isolated_environment: Path) -> None:
    with db.database(isolated_environment) as connection:
        connection.execute("UPDATE meta SET schema_version = 99")
    before = (isolated_environment / "workpilot.db").read_bytes()
    with pytest.raises(WorkPilotError):
        with db.database(isolated_environment):
            pass
    assert (isolated_environment / "workpilot.db").read_bytes() == before


def test_unversioned_user_database_is_not_claimed(isolated_environment: Path) -> None:
    isolated_environment.mkdir()
    path = isolated_environment / "workpilot.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE unrelated(value TEXT)")
    connection.commit()
    connection.close()
    before = path.read_bytes()
    with pytest.raises(WorkPilotError):
        with db.database(isolated_environment):
            pass
    assert path.read_bytes() == before


def test_database_symlink_cannot_modify_its_target(
    isolated_environment: Path, tmp_path: Path
) -> None:
    target = tmp_path / "fictional-private-file"
    target.write_text("FAKE_SENTINEL", encoding="utf-8")
    original_mode = target.stat().st_mode
    isolated_environment.mkdir()
    (isolated_environment / "workpilot.db").symlink_to(target)

    with pytest.raises(WorkPilotError):
        with db.database(isolated_environment):
            pass

    assert target.read_text() == "FAKE_SENTINEL"
    assert target.stat().st_mode == original_mode


def test_readonly_data_directory_is_not_silently_made_writable(isolated_environment: Path) -> None:
    isolated_environment.mkdir(mode=0o500)
    try:
        with pytest.raises(WorkPilotError):
            with db.database(isolated_environment):
                pass
        assert stat.S_IMODE(isolated_environment.stat().st_mode) == 0o500
        assert not (isolated_environment / "workpilot.db").exists()
    finally:
        isolated_environment.chmod(0o700)


def test_writer_conflict_returns_busy(demo_project: Path, isolated_environment: Path) -> None:
    settings = load_settings()
    with db.database(isolated_environment) as connection:
        with db.transaction(connection):
            with pytest.raises(WorkPilotError) as caught:
                ProjectRegistry(settings).add("demo", demo_project, ["STATUS.md"])
    assert caught.value.code == ExitCode.BUSY
