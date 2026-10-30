"""T03: offline, deterministic reports with explicit evidence and freshness."""

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workpilot.cli import app
from workpilot.config import EXCERPT_MAX_CHARS, MAX_FILE_BYTES, load_settings
from workpilot.domain.models import MemoryKind
from workpilot.ingestion.sync import sync_project
from workpilot.memory import MemoryStore
from workpilot.projects import ProjectRegistry
from workpilot.security.policy import SourcePolicy
from workpilot.status import project_status, render_status
from workpilot.storage.db import database
from workpilot.storage.repositories import SourceRepository

EVIDENCE = "# 虚构项目\n文件证据，仅表示同步时内容。\n"


@pytest.fixture
def indexed_project(registered_project, demo_project):
    (demo_project / "STATUS.md").write_text(EVIDENCE, encoding="utf-8")
    assert sync_project("demo", load_settings()).exit_code == 0
    return registered_project


def index_state():
    with database(load_settings().data_dir) as connection:
        return {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in ("projects", "sources", "source_chunks", "memory_entries")
        }


def forbid_source_read(*_args, **_kwargs):
    raise AssertionError("status must not read source file content or infer new facts.")


def test_status_is_offline_deterministic_ordered_and_cited(demo_project, monkeypatch):
    settings = load_settings()
    documents = {"z.md": "# 虚构 Z\n末项证据\n", "a.markdown": "# 虚构 A\n首项证据\n"}
    for name, body in documents.items():
        (demo_project / name).write_text(body, encoding="utf-8")
    ProjectRegistry(settings).add("ordered", demo_project, list(documents))
    assert sync_project("ordered", settings).exit_code == 0
    MemoryStore(settings).add("ordered", MemoryKind.NOTE, "只属于人工记忆的内容")
    before = index_state()
    with monkeypatch.context() as blocked:
        blocked.setattr(SourcePolicy, "read_source", forbid_source_read)
        blocked.setattr(os, "read", forbid_source_read)
        blocked.setattr(Path, "read_text", forbid_source_read)
        blocked.setattr(Path, "read_bytes", forbid_source_read)
        first = CliRunner().invoke(app, ["status", "ordered"])
        second = CliRunner().invoke(app, ["status", "ordered"])
    assert first.exit_code == second.exit_code == 0, first.output
    assert first.output == second.output
    output = first.output
    positions = [
        output.index(label)
        for label in ("项目：", "根目录：", "上次同步时间", "来源（", "告警：", "文件证据摘录")
    ]
    assert positions == sorted(positions)
    assert output.index("a.markdown | active") < output.index("z.md | active")
    for name, body in documents.items():
        digest = sha256(body.encode()).hexdigest()[:8]
        assert f"{name} | active | {digest} | 2 | " in output
        assert f"[{name}:L1-L2] indexed_at=" in output
    assert "在上次同步时读取到" in output
    assert "只属于人工记忆的内容" not in output
    assert index_state() == before


@pytest.mark.parametrize("missing", [False, True])
def test_never_synced_source_has_no_invented_metadata(registered_project, demo_project, missing):
    if missing:
        (demo_project / "STATUS.md").unlink()
    report = project_status("demo", load_settings())
    assert report.last_sync is None and not report.excerpts
    source = report.sources[0]
    assert source.state == ("missing" if missing else "unindexed")
    assert (source.sha_prefix, source.line_count, source.indexed_at) == (None, None, None)
    result = CliRunner().invoke(app, ["status", "demo"])
    assert result.exit_code == 0
    assert "尚无已记录的来源同步时间" in result.output
    assert "无已索引资料" in result.output
    assert "来源缺失" in result.output if missing else "尚未索引" in result.output
    assert index_state()["sources"] == []


def test_empty_document_has_zero_lines_and_no_claims(registered_project, demo_project):
    (demo_project / "STATUS.md").write_bytes(b"")
    sync_project("demo", load_settings())
    report = project_status("demo", load_settings())
    assert report.sources[0].line_count == 0
    assert report.sources[0].sha_prefix == sha256(b"").hexdigest()[:8]
    assert report.sources[0].state == "active"
    assert report.excerpts == ()
    assert "无已索引资料" in render_status(report)


@pytest.mark.parametrize("sync_missing", [False, True])
def test_missing_file_hides_cached_body_before_and_after_sync(
    indexed_project, demo_project, sync_missing
):
    previous = project_status("demo", load_settings()).sources[0]
    (demo_project / "STATUS.md").unlink()
    if sync_missing:
        assert sync_project("demo", load_settings()).exit_code == 3
    before = index_state()
    report = project_status("demo", load_settings())
    assert report.sources[0].state == "missing"
    assert report.sources[0].indexed_at == previous.indexed_at
    assert report.sources[0].sha_prefix == previous.sha_prefix
    assert not report.excerpts
    result = CliRunner().invoke(app, ["status", "demo"])
    assert result.exit_code == 0
    assert "来源缺失" in result.output and "文件证据，仅表示" not in result.output
    assert index_state() == before


@pytest.mark.parametrize("change", ["size", "mtime"])
def test_changed_metadata_marks_stale_and_hides_old_excerpt_until_sync(
    indexed_project, demo_project, change
):
    path = demo_project / "STATUS.md"
    if change == "size":
        path.write_text(
            "# 虚构变更\n变更后的证据长度与原内容不同，请重新同步。\n", encoding="utf-8"
        )
    else:
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000_000))
    before = index_state()
    report = project_status("demo", load_settings())
    assert report.sources[0].stale
    assert report.excerpts == ()
    assert "索引可能过期" in render_status(report)
    assert "旧摘录已隐藏" in render_status(report)
    assert index_state() == before
    assert sync_project("demo", load_settings()).exit_code == 0
    refreshed = project_status("demo", load_settings())
    assert not refreshed.sources[0].stale
    assert refreshed.excerpts


@pytest.mark.parametrize("seconds, stale", [(604799, False), (604800, False), (604801, True)])
def test_freshness_uses_strict_seven_day_boundary(indexed_project, seconds, stale):
    indexed_at = datetime(2026, 10, 1, tzinfo=UTC)
    with database(load_settings().data_dir) as connection:
        connection.execute("UPDATE sources SET indexed_at = '2026-10-01T00:00:00Z'")
    report = project_status("demo", load_settings(), now=indexed_at + timedelta(seconds=seconds))
    assert report.sources[0].stale is stale
    assert report.excerpts  # Old indexed evidence is labelled, not silently treated as current.
    assert ("超过 7 天" in render_status(report)) is stale


def test_stale_days_environment_is_used(indexed_project, monkeypatch):
    monkeypatch.setenv("WORKPILOT_STALE_DAYS", "2")
    timestamp = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with database(load_settings().data_dir) as connection:
        connection.execute("UPDATE sources SET indexed_at = ?", (timestamp,))
    result = CliRunner().invoke(app, ["status", "demo"])
    assert result.exit_code == 0 and "超过 2 天" in result.output


def test_last_sync_is_max_recorded_source_time_not_path_order(demo_project):
    settings = load_settings()
    ProjectRegistry(settings).add("demo", demo_project, ["STATUS.md", "STATUS_CRLF.md"])
    sync_project("demo", settings)
    with database(settings.data_dir) as connection:
        connection.execute("UPDATE sources SET indexed_at = '2026-10-01T00:00:00Z'")
        connection.execute(
            "UPDATE sources SET indexed_at = '2026-10-02T00:00:00Z' "
            "WHERE relative_path = 'STATUS.md'"
        )
    assert project_status("demo", settings).last_sync == "2026-10-02T00:00:00Z"


def test_revoked_source_is_excluded_without_reading_it(demo_project, monkeypatch):
    settings = load_settings()
    (demo_project / "revoked.md").write_text("REVOKED-CANARY", encoding="utf-8")
    project = ProjectRegistry(settings).add("demo", demo_project, ["STATUS.md", "revoked.md"])
    sync_project("demo", settings)
    with database(settings.data_dir) as connection:
        connection.execute(
            "UPDATE projects SET allowed_files_json = ? WHERE id = ?",
            (json.dumps(["STATUS.md"]), project.id),
        )
    original_inspect = SourcePolicy.inspect_source
    inspected = []

    def inspect_allowed(self, project_id, relative):
        inspected.append(relative)
        return original_inspect(self, project_id, relative)

    monkeypatch.setattr(SourcePolicy, "inspect_source", inspect_allowed)
    report = project_status("demo", settings)
    assert inspected == ["STATUS.md"]
    assert [source.relative_path for source in report.sources] == ["STATUS.md"]
    assert "已撤销授权" in render_status(report)
    assert "REVOKED-CANARY" not in render_status(report)


def test_inactive_source_cannot_display_leftover_chunks(indexed_project):
    with database(load_settings().data_dir) as connection:
        connection.execute("UPDATE sources SET active = 0")
        assert connection.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0] > 0
    report = project_status("demo", load_settings())
    assert report.sources[0].state == "inactive"
    assert report.excerpts == ()
    assert "来源索引已失效" in render_status(report)
    assert "无已索引资料" in render_status(report)


@pytest.mark.parametrize(
    "replacement", ["symlink", "dangling", "directory", "fifo", "hardlink", "large"]
)
def test_unsafe_source_replacement_fails_closed_without_cached_body(
    indexed_project, demo_project, tmp_path, replacement
):
    path = demo_project / "STATUS.md"
    if replacement == "hardlink":
        os.link(path, tmp_path / "fictional-link.md")
    elif replacement == "large":
        path.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    else:
        path.unlink()
        if replacement in {"symlink", "dangling"}:
            target = tmp_path / "fictional-target.md"
            if replacement == "symlink":
                target.write_text("OUTSIDE-CANARY", encoding="utf-8")
            path.symlink_to(target)
        elif replacement == "directory":
            path.mkdir()
        else:
            os.mkfifo(path)
    before = index_state()
    result = CliRunner().invoke(app, ["status", "demo"])
    assert result.exit_code == 4, result.output
    assert "文件证据，仅表示" not in result.output and "CANARY" not in result.output
    assert index_state() == before


@pytest.mark.parametrize(
    "relative", ["../outside.md", ".env.md", "nested/.git/notes.md", "notes.txt"]
)
def test_unsafe_whitelist_cannot_expose_cached_text(indexed_project, relative):
    with database(load_settings().data_dir) as connection:
        connection.execute("UPDATE projects SET allowed_files_json = ?", (json.dumps([relative]),))
    result = CliRunner().invoke(app, ["status", "demo"])
    assert result.exit_code == 4 and "文件证据，仅表示" not in result.output


def test_missing_project_root_is_reported_without_progress(indexed_project, demo_project, tmp_path):
    demo_project.rename(tmp_path / "fictional-moved-root")
    result = CliRunner().invoke(app, ["status", "demo"])
    assert result.exit_code == 0 and "来源缺失" in result.output
    assert "文件证据，仅表示" not in result.output


@pytest.mark.parametrize("removed", [False, True])
def test_unknown_or_removed_project_returns_three_before_source_checks(
    registered_project, monkeypatch, removed
):
    if removed:
        ProjectRegistry(load_settings()).remove("demo")
    monkeypatch.setattr(SourcePolicy, "inspect_source", forbid_source_read)
    result = CliRunner().invoke(app, ["status", "demo" if removed else "unknown"])
    assert result.exit_code == 3


def test_change_during_metadata_check_returns_busy_without_body(
    indexed_project, demo_project, monkeypatch
):
    original_open = os.open

    def open_then_change(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == "STATUS.md":
            (demo_project / "STATUS.md").write_text("虚构并发变更", encoding="utf-8")
        return descriptor

    monkeypatch.setattr(os, "open", open_then_change)
    result = CliRunner().invoke(app, ["status", "demo"])
    assert result.exit_code == 7
    assert "文件证据，仅表示" not in result.output and "虚构并发变更" not in result.output


def test_policy_failure_after_an_earlier_source_does_not_print_partial_evidence(demo_project):
    settings = load_settings()
    for name in ("a.md", "z.md"):
        (demo_project / name).write_text("CACHED-CANARY", encoding="utf-8")
    ProjectRegistry(settings).add("demo", demo_project, ["a.md", "z.md"])
    sync_project("demo", settings)
    (demo_project / "z.md").unlink()
    (demo_project / "z.md").symlink_to(demo_project / "a.md")
    result = CliRunner().invoke(app, ["status", "demo"])
    assert result.exit_code == 4
    assert "CACHED-CANARY" not in result.output


def test_report_metadata_and_body_share_one_sqlite_snapshot(indexed_project, monkeypatch):
    settings = load_settings()
    original_first = SourceRepository.first_chunk
    old_time = project_status("demo", settings).last_sync
    changed = False

    def first_after_concurrent_write(self, project_id, relative):
        nonlocal changed
        if not changed:
            changed = True
            writer = sqlite3.connect(settings.database_path, isolation_level=None, timeout=0)
            try:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("UPDATE sources SET indexed_at = '2026-01-01T00:00:00Z'")
                writer.execute("UPDATE source_chunks SET body = '另一个已提交快照'")
                writer.commit()
            finally:
                writer.close()
        return original_first(self, project_id, relative)

    monkeypatch.setattr(SourceRepository, "first_chunk", first_after_concurrent_write)
    first = project_status("demo", settings)
    assert first.last_sync == old_time and first.excerpts[0].body == EVIDENCE
    second = project_status("demo", settings)
    assert second.last_sync == "2026-01-01T00:00:00Z"
    assert second.excerpts[0].body == "另一个已提交快照"


def test_invalid_index_time_is_a_safe_configuration_error(indexed_project):
    with database(load_settings().data_dir) as connection:
        connection.execute("UPDATE sources SET indexed_at = 'BROKEN-CANARY'")
    result = CliRunner().invoke(app, ["status", "demo"])
    assert result.exit_code == 6
    assert "CANARY" not in result.output and "文件证据，仅表示" not in result.output


def test_excerpt_is_bounded_and_terminal_controls_are_literal(registered_project, demo_project):
    body = "# 虚构\n\x1b[2J" + "文" * 500
    (demo_project / "STATUS.md").write_text(body, encoding="utf-8")
    sync_project("demo", load_settings())
    report = project_status("demo", load_settings())
    assert len(report.excerpts[0].body) == EXCERPT_MAX_CHARS
    assert report.excerpts[0].body.endswith("…")
    result = CliRunner().invoke(app, ["status", "demo"], color=True)
    assert result.exit_code == 0
    assert "\\x1b[2J" in result.output and "\x1b[2J" not in result.output
