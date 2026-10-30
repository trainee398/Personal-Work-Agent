"""T02 / §§2, 8, 10: real process contention, persistent inode and cleanup."""

import os
import select
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workpilot.cli import app
from workpilot.config import load_settings
from workpilot.ingestion.sync import sync_project
from workpilot.security.policy import SourcePolicy
from workpilot.storage.db import database
from workpilot.storage.locking import sync_lock


def test_second_process_returns_seven_without_waiting_for_lock_release(registered_project):
    settings = load_settings()
    program = (
        "import sys\nfrom pathlib import Path\n"
        "from workpilot.storage.locking import sync_lock\n"
        "with sync_lock(Path(sys.argv[1])):\n"
        "    print('locked', flush=True)\n"
        "    sys.stdin.readline()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program, str(settings.data_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 5)[0], "Lock holder did not start."
        assert process.stdout.readline().strip() == "locked"
        executable = Path(sys.executable).with_name("workpilot")
        result = subprocess.run(
            [str(executable), "sync", "demo"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 7, result.stdout + result.stderr
        assert "未等待锁" in result.stderr
        assert process.poll() is None  # The lock is still held when the second command exits.
        with database(settings.data_dir) as connection:
            assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0] == 0
    finally:
        try:
            process.communicate("\n", timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
    assert process.returncode == 0
    assert sync_project("demo", settings).indexed == 1


def test_lock_contention_never_reads_sources(registered_project, monkeypatch):
    settings = load_settings()

    def forbid_read(*_args):
        raise AssertionError("A busy sync must not open a source.")

    monkeypatch.setattr(SourcePolicy, "read_source", forbid_read)
    with sync_lock(settings.data_dir):
        result = CliRunner().invoke(app, ["sync", "demo"])
    assert result.exit_code == 7


def test_lock_inode_persists_and_permissions_are_private(registered_project):
    settings = load_settings()
    sync_project("demo", settings)
    first = settings.lock_path.stat()
    sync_project("demo", settings)
    second = settings.lock_path.stat()
    assert first.st_ino == second.st_ino
    assert stat.S_IMODE(second.st_mode) == 0o600
    assert settings.lock_path.read_bytes() == b""


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_lock_cannot_read_or_chmod_its_target(registered_project, tmp_path, kind):
    settings = load_settings()
    target = tmp_path / "fictional-lock-target"
    target.write_text("CANARY-DO-NOT-LEAK", encoding="utf-8")
    before = target.stat().st_mode
    if kind == "symlink":
        settings.lock_path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, settings.lock_path)
    else:
        os.mkfifo(settings.lock_path)
    result = CliRunner().invoke(app, ["sync", "demo"])
    assert result.exit_code == 6
    assert target.read_text() == "CANARY-DO-NOT-LEAK"
    assert target.stat().st_mode == before
    assert "CANARY" not in result.output


def test_lock_releases_on_body_error_without_mislabeling_it(registered_project):
    settings = load_settings()
    with pytest.raises(OSError, match="fictional body failure"), sync_lock(settings.data_dir):
        raise OSError("fictional body failure")
    with sync_lock(settings.data_dir):
        pass
