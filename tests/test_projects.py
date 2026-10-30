"""Project metadata persists; removal never writes to the source project."""

import hashlib
import re
from pathlib import Path
from uuid import UUID

import pytest

from workpilot.config import load_settings
from workpilot.errors import ExitCode, NotFoundError, WorkPilotError
from workpilot.projects import ProjectRegistry
from workpilot.storage.db import database


def _seed_related(data_dir: Path, project_id: str) -> None:
    """Synthetic rows exercise removal; this helper is not an indexer."""
    with database(data_dir) as connection:
        connection.execute(
            """INSERT INTO sources(id, project_id, relative_path, sha256, indexed_at)
            VALUES ('source', ?, 'STATUS.md', ?, '2026-01-01T00:00:00Z')""",
            (project_id, "a" * 64),
        )
        connection.execute(
            """INSERT INTO source_chunks(id, source_id, line_start, line_end, body, content_hash)
            VALUES ('chunk', 'source', 1, 1, '虚构旧证据', ?)""",
            ("b" * 64,),
        )
        connection.execute(
            """INSERT INTO memory_entries(id, project_id, kind, body, created_at, updated_at)
            VALUES ('memory', ?, 'decision', '虚构人工决定',
                    '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""",
            (project_id,),
        )
        connection.execute(
            """INSERT INTO runs(id, project_id, action, status, started_at)
            VALUES ('run', ?, 'init', 'success', '2026-01-01T00:00:00Z')""",
            (project_id,),
        )
        connection.execute(
            """INSERT INTO tool_events(id, run_id, tool_name, permission, outcome, created_at)
            VALUES ('event', 'run', 'fictional_tool', 'read', 'ok', '2026-01-01T00:00:00Z')"""
        )


def test_registration_survives_a_new_registry(
    demo_project: Path, isolated_environment: Path
) -> None:
    settings = load_settings()
    project = ProjectRegistry(settings).add(
        "虚构项目", demo_project, ["STATUS_CRLF.md", "STATUS.md", "./STATUS.md"]
    )

    assert project.root == demo_project.resolve()
    assert project.allowed_files == ("STATUS.md", "STATUS_CRLF.md")
    assert UUID(project.id).version == 4
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", project.created_at)
    assert ProjectRegistry(load_settings()).get("虚构项目") == project
    with database(isolated_environment) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0] == 0


def test_duplicate_name_does_not_change_the_original(demo_project: Path) -> None:
    registry = ProjectRegistry(load_settings())
    original = registry.add("demo", demo_project, ["STATUS.md"])
    with pytest.raises(WorkPilotError) as caught:
        registry.add("demo", demo_project, ["STATUS_CRLF.md"])
    assert caught.value.code == ExitCode.USAGE
    assert registry.get("demo") == original


def test_list_order_is_stable(demo_project: Path) -> None:
    registry = ProjectRegistry(load_settings())
    registry.add("z-demo", demo_project, ["STATUS.md"])
    registry.add("a-demo", demo_project, ["STATUS.md"])
    assert [project.name for project in registry.list()] == ["a-demo", "z-demo"]


@pytest.mark.parametrize("kind", ["missing", "file", "parent-traversal"])
def test_invalid_root_is_rejected_before_storage_creation(
    kind: str, demo_project: Path, isolated_environment: Path
) -> None:
    roots = {
        "missing": demo_project / "not-created",
        "file": demo_project / "STATUS.md",
        "parent-traversal": demo_project / ".." / demo_project.name,
    }
    with pytest.raises(WorkPilotError) as caught:
        ProjectRegistry(load_settings()).add("demo", roots[kind], ["STATUS.md"])
    assert caught.value.code == (ExitCode.DENIED if kind == "parent-traversal" else ExitCode.USAGE)
    assert not isolated_environment.exists()


@pytest.mark.parametrize(
    "relative",
    [
        "../outside.md",
        ".env",
        ".env.md",
        "private.pem",
        "private.key",
        "id_rsa.md",
        "id_ed25519.md",
        "service-credential.md",
        "archive.p12",
        "store.keystore",
        ".ssh/config.md",
        ".git/notes.md",
        ".DS_Store",
        "notes.txt",
        "C:\\outside.md",
        "folder\\outside.md",
    ],
)
def test_disallowed_names_are_denied_even_when_explicitly_registered(
    relative: str, demo_project: Path, isolated_environment: Path
) -> None:
    with pytest.raises(WorkPilotError) as caught:
        ProjectRegistry(load_settings()).add("demo", demo_project, [relative])
    assert caught.value.code == ExitCode.DENIED
    assert not isolated_environment.exists()


def test_absolute_file_path_is_denied(demo_project: Path) -> None:
    with pytest.raises(WorkPilotError) as caught:
        ProjectRegistry(load_settings()).add(
            "demo", demo_project, [str(demo_project / "STATUS.md")]
        )
    assert caught.value.code == ExitCode.DENIED


def test_missing_file_returns_not_found(demo_project: Path, isolated_environment: Path) -> None:
    with pytest.raises(NotFoundError):
        ProjectRegistry(load_settings()).add("demo", demo_project, ["MISSING.md"])
    assert not isolated_environment.exists()


def test_directory_disguised_as_markdown_is_denied(demo_project: Path) -> None:
    (demo_project / "directory.md").mkdir()
    with pytest.raises(WorkPilotError) as caught:
        ProjectRegistry(load_settings()).add("demo", demo_project, ["directory.md"])
    assert caught.value.code == ExitCode.DENIED


def test_final_symlink_and_escaping_parent_are_denied(demo_project: Path, tmp_path: Path) -> None:
    (demo_project / "alias.md").symlink_to(demo_project / "STATUS.md")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "STATUS.md").write_text("纯虚构越界样例", encoding="utf-8")
    (demo_project / "outside").symlink_to(outside, target_is_directory=True)

    for relative in ("alias.md", "outside/STATUS.md"):
        with pytest.raises(WorkPilotError) as caught:
            ProjectRegistry(load_settings()).add("demo", demo_project, [relative])
        assert caught.value.code == ExitCode.DENIED


def test_root_alias_is_stored_as_canonical_path(demo_project: Path, tmp_path: Path) -> None:
    alias = tmp_path / "project-alias"
    alias.symlink_to(demo_project, target_is_directory=True)
    result = ProjectRegistry(load_settings()).add("demo", alias, ["STATUS.md"])
    assert result.root == demo_project.resolve()


def test_data_directory_cannot_be_inside_source_project(demo_project: Path) -> None:
    settings = load_settings().model_copy(update={"data_dir": demo_project / "runtime-data"})
    with pytest.raises(WorkPilotError) as caught:
        ProjectRegistry(settings).add("demo", demo_project, ["STATUS.md"])
    assert caught.value.code == ExitCode.DENIED
    assert not settings.data_dir.exists()


def test_remove_invalidates_evidence_but_preserves_memories(
    demo_project: Path, isolated_environment: Path
) -> None:
    registry = ProjectRegistry(load_settings())
    project = registry.add("demo", demo_project, ["STATUS.md"])
    _seed_related(isolated_environment, project.id)
    registry.remove("demo")

    assert registry.list() == []
    with pytest.raises(NotFoundError):
        registry.get("demo")
    with pytest.raises(WorkPilotError) as caught:
        registry.add("demo", demo_project, ["STATUS.md"])
    assert caught.value.code == ExitCode.USAGE
    with database(isolated_environment) as connection:
        assert connection.execute("SELECT active FROM projects").fetchone()[0] == 0
        assert connection.execute("SELECT active FROM sources").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0] == 1


def test_purge_clears_data_and_retains_detached_audit_metadata(
    demo_project: Path, isolated_environment: Path
) -> None:
    registry = ProjectRegistry(load_settings())
    project = registry.add("demo", demo_project, ["STATUS.md"])
    _seed_related(isolated_environment, project.id)
    registry.remove("demo")
    registry.purge("demo", expected_id=project.id)

    with database(isolated_environment) as connection:
        for table in ("projects", "sources", "source_chunks", "memory_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute("SELECT project_id FROM runs").fetchone()[0] is None
        assert connection.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0] == 1
    assert registry.add("demo", demo_project, ["STATUS.md"]).id != project.id


def test_purge_is_atomic_when_a_delete_fails(
    demo_project: Path, isolated_environment: Path
) -> None:
    registry = ProjectRegistry(load_settings())
    project = registry.add("demo", demo_project, ["STATUS.md"])
    _seed_related(isolated_environment, project.id)
    with database(isolated_environment) as connection:
        connection.execute(
            """CREATE TRIGGER fail_purge BEFORE DELETE ON projects
            BEGIN SELECT RAISE(ABORT, 'fictional failure'); END"""
        )
    with pytest.raises(WorkPilotError):
        registry.purge("demo", expected_id=project.id)
    with database(isolated_environment) as connection:
        for table in ("projects", "sources", "source_chunks", "memory_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        assert connection.execute("SELECT project_id FROM runs").fetchone()[0] == project.id


def test_registry_does_not_read_or_modify_source_contents(
    demo_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = [demo_project / name for name in ("STATUS.md", "STATUS_CRLF.md", ".env")]
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path.is_relative_to(demo_project):
            raise AssertionError("Registration must not open source files.")
        return original_open(path, *args, **kwargs)

    registry = ProjectRegistry(load_settings())
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded_open)
        project = registry.add("demo", demo_project, ["STATUS.md"])
        registry.list()
        registry.remove("demo")
        registry.purge("demo", expected_id=project.id)
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    assert after == before
