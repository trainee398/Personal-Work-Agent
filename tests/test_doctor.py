"""Real offline diagnostics, including unhealthy and locked environments."""

import fcntl
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workpilot.cli import app
from workpilot.config import load_settings
from workpilot.doctor import inspect_environment
from workpilot.projects import ProjectRegistry
from workpilot.storage.db import database


def test_doctor_reports_uninitialized_data_without_creating_it(isolated_environment: Path) -> None:
    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 6
    assert "workpilot init" in result.output
    assert not isolated_environment.exists()


def test_doctor_does_not_probe_a_symlinked_data_directory(
    isolated_environment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "fictional-outside-data"
    target.mkdir()
    isolated_environment.symlink_to(target, target_is_directory=True)

    def forbidden_access(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("Doctor must stop before probing a symlink target.")

    monkeypatch.setattr(os, "access", forbidden_access)
    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 6
    assert "目录类型或属主无效" in result.output
    assert list(target.iterdir()) == []


def test_doctor_is_offline_and_never_prints_the_key(
    isolated_environment: Path, demo_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_key = "FAKE_API_KEY_NOT_FOR_OUTPUT"
    monkeypatch.setenv("OPENAI_API_KEY", fake_key)
    monkeypatch.setenv("WORKPILOT_MODEL", "fictional-model")
    ProjectRegistry(load_settings()).add("demo", demo_project, ["STATUS.md"])
    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    for text in (
        "Python",
        "SQLite",
        "FTS5",
        "integrity_check",
        "schema_version: 1",
        "tracing",
        "OPENAI_API_KEY: 已设置",
        "WORKPILOT_MODEL: fictional-model",
        "WORKPILOT_SEND_TO_MODEL: false",
        "count.projects: 1",
        "count.source_chunks: 0",
        "count.memory_entries: 0",
    ):
        assert text in result.output
    assert "count.runs: 0" in result.output
    assert "count.tool_events: 0" in result.output
    assert "0700" in result.output and "0600" in result.output
    assert fake_key not in result.output
    assert "CANARY-DO-NOT-LEAK" not in result.output
    assert not (isolated_environment / "workpilot.lock").exists()


def test_doctor_rejects_invalid_configuration_without_echoing_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKPILOT_STALE_DAYS", "FAKE_PRIVATE_INPUT")
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 6
    assert "WORKPILOT_STALE_DAYS" in result.output
    assert "FAKE_PRIVATE_INPUT" not in result.output


def test_doctor_reports_corruption_without_exposing_database_bytes(
    isolated_environment: Path,
) -> None:
    isolated_environment.mkdir(mode=0o700)
    path = isolated_environment / "workpilot.db"
    path.write_bytes(b"FAKE_CORRUPT_CANARY_DO_NOT_PRINT")
    path.chmod(0o600)
    before = path.read_bytes()

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 6
    assert "FAKE_CORRUPT_CANARY_DO_NOT_PRINT" not in result.output
    assert path.read_bytes() == before


def test_doctor_does_not_repair_permissions(isolated_environment: Path) -> None:
    with database(isolated_environment):
        pass
    isolated_environment.chmod(0o755)
    path = isolated_environment / "workpilot.db"
    path.chmod(0o644)

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 6
    assert "0755" in result.output and "0644" in result.output
    assert stat.S_IMODE(isolated_environment.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_doctor_reports_lock_contention_without_waiting(isolated_environment: Path) -> None:
    with database(isolated_environment):
        pass
    lock_path = isolated_environment / "workpilot.lock"
    with lock_path.open("w") as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = CliRunner().invoke(app, ["doctor"])
        finally:
            fcntl.flock(locked, fcntl.LOCK_UN)
    assert result.exit_code == 0, result.output
    assert "[WARN] lock: 已占用" in result.output


def test_doctor_rejects_lock_symlink(isolated_environment: Path, tmp_path: Path) -> None:
    with database(isolated_environment):
        pass
    target = tmp_path / "fictional-sentinel"
    target.write_text("FAKE_VALUE")
    (isolated_environment / "workpilot.lock").symlink_to(target)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 6
    assert "锁文件不可安全访问" in result.output
    assert target.read_text() == "FAKE_VALUE"


def test_doctor_checks_stored_registration_configuration(
    isolated_environment: Path, demo_project: Path
) -> None:
    ProjectRegistry(load_settings()).add("demo", demo_project, ["STATUS.md"])
    with database(isolated_environment) as connection:
        connection.execute("UPDATE projects SET allowed_files_json = 'FAKE_INVALID_JSON'")
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 6
    assert "登记数据无效" in result.output
    assert "FAKE_INVALID_JSON" not in result.output


def test_doctor_does_not_require_fts5(
    isolated_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workpilot import doctor

    with database(isolated_environment):
        pass
    monkeypatch.setattr(doctor, "_fts5_available", lambda: False)
    report = inspect_environment()
    assert report.exit_code == 0
    assert any(check.name == "FTS5" and check.state == "WARN" for check in report.checks)
