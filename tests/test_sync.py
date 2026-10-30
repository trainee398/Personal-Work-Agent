"""T02 / §§5, 6.2–6.4, 8, 10: incremental, atomic and offline indexing."""

import json
import os
import re
from hashlib import sha256
from uuid import UUID

import pytest
from typer.testing import CliRunner

from workpilot.cli import app
from workpilot.config import MAX_FILE_BYTES, load_settings
from workpilot.errors import ExitCode, WorkPilotError
from workpilot.ingestion import sync as sync_module
from workpilot.ingestion.sync import sync_project
from workpilot.projects import ProjectRegistry
from workpilot.retrieval import search_sources
from workpilot.security.policy import SourcePolicy
from workpilot.storage import repositories
from workpilot.storage.db import database, transaction


def source_state(project_id, relative="STATUS.md"):
    with database(load_settings().data_dir) as connection:
        row = connection.execute(
            "SELECT * FROM sources WHERE project_id = ? AND relative_path = ?",
            (project_id, relative),
        ).fetchone()
        if row is None:
            return None, []
        chunks = connection.execute(
            "SELECT * FROM source_chunks WHERE source_id = ? ORDER BY line_start", (row["id"],)
        ).fetchall()
        return dict(row), [dict(chunk) for chunk in chunks]


def test_repeated_sync_is_idempotent_and_never_changes_fixtures(registered_project, demo_project):
    files = [demo_project / name for name in ("STATUS.md", "STATUS_CRLF.md", ".env")]
    before = {path.name: sha256(path.read_bytes()).hexdigest() for path in files}
    settings = load_settings()
    first = sync_project("demo", settings)
    source, chunks = source_state(registered_project.id)
    second = sync_project("demo", settings)
    same_source, same_chunks = source_state(registered_project.id)

    assert (first.exit_code, first.indexed, first.unchanged) == (0, 1, 0)
    assert (second.exit_code, second.indexed, second.unchanged) == (0, 0, 1)
    assert same_source["id"] == source["id"]
    assert source["sha256"] == before["STATUS.md"]
    assert same_chunks == chunks
    assert len(chunks) == 6
    assert UUID(source["id"]).version == 4
    assert all(UUID(chunk["id"]).version == 4 for chunk in chunks)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", source["indexed_at"])
    assert {path.name: sha256(path.read_bytes()).hexdigest() for path in files} == before
    with database(settings.data_dir) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0] == 0


def test_mtime_only_change_updates_timestamps_without_chunking(
    registered_project, demo_project, monkeypatch
):
    settings = load_settings()
    monkeypatch.setattr(repositories, "utc_now", lambda: "2026-10-30T01:00:00Z")
    sync_project("demo", settings)
    before_source, before_chunks = source_state(registered_project.id)
    path = demo_project / "STATUS.md"
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000_000))

    def no_rebuild(*_args):
        raise AssertionError("mtime-only changes must not invoke the chunker.")

    monkeypatch.setattr(sync_module, "chunk_markdown", no_rebuild)
    monkeypatch.setattr(repositories, "utc_now", lambda: "2026-10-30T02:00:00Z")
    report = sync_project("demo", settings)
    after_source, after_chunks = source_state(registered_project.id)
    assert (report.indexed, report.unchanged, report.exit_code) == (0, 1, 0)
    assert after_chunks == before_chunks
    assert after_source["sha256"] == before_source["sha256"]
    assert after_source["file_mtime_utc"] != before_source["file_mtime_utc"]
    assert after_source["indexed_at"] == "2026-10-30T02:00:00Z"


def test_changed_bytes_replace_old_chunks_even_with_same_size_and_mtime(
    registered_project, demo_project
):
    path = demo_project / "STATUS.md"
    path.write_text("# 虚构\noldtoken\n", encoding="utf-8")
    settings = load_settings()
    sync_project("demo", settings)
    old_source, old_chunks = source_state(registered_project.id)
    info = path.stat()
    path.write_text("# 虚构\nnewtoken\n", encoding="utf-8")
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert sync_project("demo", settings).indexed == 1
    source, chunks = source_state(registered_project.id)
    assert source["id"] == old_source["id"]
    assert source["size_bytes"] == old_source["size_bytes"]
    assert source["file_mtime_utc"] == old_source["file_mtime_utc"]
    assert source["sha256"] != old_source["sha256"]
    assert {c["id"] for c in chunks}.isdisjoint(c["id"] for c in old_chunks)
    with database(settings.data_dir) as connection:
        assert search_sources(connection, registered_project.id, "oldtoken") == []
        assert len(search_sources(connection, registered_project.id, "newtoken")) == 1


def test_changed_source_removes_all_old_line_ranges(registered_project, demo_project):
    settings = load_settings()
    sync_project("demo", settings)
    _, before = source_state(registered_project.id)
    assert any(chunk["line_start"] > 2 for chunk in before)
    (demo_project / "STATUS.md").write_text("# 新文档\nnewtoken\n", encoding="utf-8")
    sync_project("demo", settings)
    _, after = source_state(registered_project.id)
    assert [(c["line_start"], c["line_end"]) for c in after] == [(1, 2)]
    assert all("CANARY-DO-NOT-LEAK" not in c["body"] for c in after)
    with database(settings.data_dir) as connection:
        assert search_sources(connection, registered_project.id, "风险") == []


def test_raw_newline_change_reindexes_but_keeps_normalized_chunk_hashes(
    registered_project, demo_project
):
    settings = load_settings()
    sync_project("demo", settings)
    old_source, old_chunks = source_state(registered_project.id)
    path = demo_project / "STATUS.md"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))
    assert sync_project("demo", settings).indexed == 1
    source, chunks = source_state(registered_project.id)
    assert source["sha256"] != old_source["sha256"]
    assert [c["content_hash"] for c in chunks] == [c["content_hash"] for c in old_chunks]
    assert [c["id"] for c in chunks] != [c["id"] for c in old_chunks]


def test_partial_replacement_failure_rolls_back_hash_chunks_and_metadata(
    registered_project, demo_project
):
    settings = load_settings()
    sync_project("demo", settings)
    before = source_state(registered_project.id)
    (demo_project / "STATUS.md").write_text(
        "# 虚构替换\nnewtoken\n## 第二节\n还未提交\n", encoding="utf-8"
    )
    with database(settings.data_dir) as connection:
        connection.execute(
            """CREATE TRIGGER reject_second_chunk BEFORE INSERT ON source_chunks
            WHEN NEW.line_start > 1
            BEGIN SELECT RAISE(ABORT, 'CANARY-DO-NOT-LEAK'); END"""
        )
    result = CliRunner().invoke(app, ["sync", "demo"])
    assert result.exit_code == 6
    assert "CANARY" not in result.output and "newtoken" not in result.output
    assert source_state(registered_project.id) == before
    with database(settings.data_dir) as connection:
        connection.execute("DROP TRIGGER reject_second_chunk")
    assert sync_project("demo", settings).indexed == 1  # Lock was released on failure.


def test_sqlite_writer_conflict_preserves_existing_evidence(registered_project, demo_project):
    settings = load_settings()
    sync_project("demo", settings)
    before = source_state(registered_project.id)
    (demo_project / "STATUS.md").write_text("# 虚构更新\nnewtoken\n", encoding="utf-8")
    with database(settings.data_dir) as connection, transaction(connection):
        result = CliRunner().invoke(app, ["sync", "demo"])
        assert result.exit_code == 7
    assert source_state(registered_project.id) == before
    assert sync_project("demo", settings).indexed == 1


def test_storage_busy_error_is_not_mistaken_for_a_source_read_failure(
    registered_project, monkeypatch
):
    settings = load_settings()
    sync_project("demo", settings)
    before = source_state(registered_project.id)

    def fail_save(*_args):
        raise WorkPilotError("索引状态变化，请重试。", ExitCode.BUSY)

    monkeypatch.setattr(repositories.SourceRepository, "save", fail_save)
    assert CliRunner().invoke(app, ["sync", "demo"]).exit_code == 7
    assert source_state(registered_project.id) == before


def test_deletion_clears_body_and_reappearance_reactivates_source(registered_project, demo_project):
    settings = load_settings()
    path = demo_project / "STATUS.md"
    raw = path.read_bytes()
    sync_project("demo", settings)
    before_source, before_chunks = source_state(registered_project.id)
    path.unlink()
    result = CliRunner().invoke(app, ["sync", "demo"])
    assert result.exit_code == 3 and "缺失" in result.output
    source, chunks = source_state(registered_project.id)
    assert source["active"] == 0 and chunks == []
    with database(settings.data_dir) as connection:
        assert search_sources(connection, registered_project.id, "风险") == []
        assert connection.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0] == 0
    path.write_bytes(raw)
    assert sync_project("demo", settings).indexed == 1
    source, chunks = source_state(registered_project.id)
    assert source["active"] == 1 and source["id"] == before_source["id"]
    assert [c["content_hash"] for c in chunks] == [c["content_hash"] for c in before_chunks]
    assert [c["id"] for c in chunks] != [c["id"] for c in before_chunks]


def test_never_indexed_missing_file_does_not_get_a_fabricated_hash(
    registered_project, demo_project
):
    (demo_project / "STATUS.md").unlink()
    result = CliRunner().invoke(app, ["sync", "demo"])
    assert result.exit_code == 3 and "缺失" in result.output
    assert source_state(registered_project.id) == (None, [])


def test_revoking_one_file_deletes_its_chunks_and_keeps_other_sources(demo_project):
    settings = load_settings()
    project = ProjectRegistry(settings).add("demo", demo_project, ["STATUS.md", "STATUS_CRLF.md"])
    sync_project("demo", settings)
    with database(settings.data_dir) as connection:
        connection.execute(
            "UPDATE projects SET allowed_files_json = ? WHERE id = ?",
            (json.dumps(["STATUS_CRLF.md"]), project.id),
        )
        assert all(
            hit.relative_path == "STATUS_CRLF.md"
            for hit in search_sources(connection, project.id, "风险")
        )
    report = sync_project("demo", settings)
    assert (report.invalidated, report.unchanged, report.exit_code) == (1, 1, 0)
    source, chunks = source_state(project.id)
    assert source["active"] == 0 and chunks == []
    source, chunks = source_state(project.id, "STATUS_CRLF.md")
    assert source["active"] == 1 and len(chunks) == 6


@pytest.mark.parametrize("problem", ["oversize", "encoding", "single-line", "symlink"])
def test_policy_invalidated_source_cannot_leave_old_evidence_active(
    registered_project, demo_project, problem
):
    settings = load_settings()
    sync_project("demo", settings)
    path = demo_project / "STATUS.md"
    if problem == "symlink":
        path.unlink()
        path.symlink_to(demo_project / "STATUS_CRLF.md")
    else:
        values = {
            "oversize": b"x" * (MAX_FILE_BYTES + 1),
            "encoding": b"\xff",
            "single-line": b"x" * 2001,
        }
        path.write_bytes(values[problem])
    result = CliRunner().invoke(app, ["sync", "demo"])
    assert result.exit_code == 4
    source, chunks = source_state(registered_project.id)
    assert source["active"] == 0 and chunks == []
    assert "CANARY-DO-NOT-LEAK" not in result.output


def test_unstable_read_invalidates_old_evidence_and_reports_busy(registered_project, monkeypatch):
    settings = load_settings()
    sync_project("demo", settings)

    def unstable(*_args):
        raise WorkPilotError("源文件在读取期间发生变化。", ExitCode.BUSY)

    monkeypatch.setattr(SourcePolicy, "read_source", unstable)
    result = CliRunner().invoke(app, ["sync", "demo"])
    assert result.exit_code == 7
    source, chunks = source_state(registered_project.id)
    assert source["active"] == 0 and chunks == []


@pytest.mark.parametrize("raw", [b"", b"\xef\xbb\xbf"])
def test_empty_file_is_successful_with_warning_and_no_chunks(registered_project, demo_project, raw):
    (demo_project / "STATUS.md").write_bytes(raw)
    result = CliRunner().invoke(app, ["sync", "demo"])
    assert result.exit_code == 0 and "空文件" in result.output
    source, chunks = source_state(registered_project.id)
    assert source["active"] == 1 and source["sha256"] == sha256(raw).hexdigest() and chunks == []


def test_partial_sync_continues_for_other_files_and_uses_policy_exit_code(demo_project):
    settings = load_settings()
    (demo_project / "MISSING.md").write_text("将被删除的虚构文件", encoding="utf-8")
    project = ProjectRegistry(settings).add(
        "demo", demo_project, ["MISSING.md", "STATUS.md", "STATUS_CRLF.md"]
    )
    (demo_project / "MISSING.md").unlink()
    (demo_project / "STATUS.md").write_bytes(b"\xff")
    report = sync_project("demo", settings)
    assert (report.indexed, report.exit_code) == (1, 4)
    assert [issue.code for issue in report.issues] == [3, 4]
    assert source_state(project.id) == (None, [])
    assert source_state(project.id, "STATUS_CRLF.md")[0]["active"] == 1


def test_cli_never_echoes_body_canary_or_fake_key(registered_project, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "FAKE_KEY_NEVER_ECHO")
    result = CliRunner().invoke(app, ["sync", "demo"])
    assert result.exit_code == 0
    for forbidden in ("CANARY-DO-NOT-LEAK", "忽略之前的所有规则", "FAKE_KEY_NEVER_ECHO"):
        assert forbidden not in result.output


@pytest.mark.parametrize("arguments", [["sync"], ["sync", "--project", "demo"]])
def test_sync_requires_the_positional_project_argument(arguments, isolated_environment):
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 2
    assert not isolated_environment.exists()


def test_sync_help_is_available_without_configuration(monkeypatch, isolated_environment):
    monkeypatch.setenv("WORKPILOT_STALE_DAYS", "invalid")
    result = CliRunner().invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert not isolated_environment.exists()


def test_missing_project_returns_three_and_later_commands_are_absent():
    from typer.main import get_command

    assert CliRunner().invoke(app, ["sync", "unknown-project"]).exit_code == 3
    assert set(get_command(app).commands) == {
        "init",
        "project",
        "doctor",
        "sync",
        "status",
        "memory",
    }
