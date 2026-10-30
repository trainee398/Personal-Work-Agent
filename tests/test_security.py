"""T02 / §§3, 6.1, 10: reauthorize and securely read only fictional files."""

import json
import os
from hashlib import sha256
from pathlib import Path

import pytest

from workpilot.config import MAX_FILE_BYTES, load_settings
from workpilot.errors import ExitCode, NotFoundError, PolicyError, WorkPilotError
from workpilot.projects import ProjectRegistry
from workpilot.security.policy import SourcePolicy
from workpilot.storage.db import database


@pytest.fixture
def reader(registered_project):
    settings = load_settings()
    with database(settings.data_dir) as connection:
        yield SourcePolicy(connection, settings)


def forbid_read(*_args, **_kwargs):
    raise AssertionError("A denied source must never reach os.read.")


def set_whitelist(reader, project_id, paths):
    reader.projects.connection.execute(
        "UPDATE projects SET allowed_files_json = ? WHERE id = ?",
        (json.dumps(paths), project_id),
    )


@pytest.mark.parametrize("project_id", ["unregistered", "removed"])
def test_project_existence_is_checked_before_paths(
    project_id, reader, registered_project, monkeypatch
):
    if project_id == "removed":
        reader.projects.remove(registered_project.name)
        project_id = registered_project.id
    monkeypatch.setattr(Path, "resolve", forbid_read)
    with pytest.raises(NotFoundError):
        reader.read_source(project_id, "../never-open.md")


@pytest.mark.parametrize("relative", ["STATUS_CRLF.md", "./STATUS.md", ".env"])
def test_exact_whitelist_is_required(relative, reader, registered_project, monkeypatch):
    monkeypatch.setattr(os, "read", forbid_read)
    with pytest.raises(PolicyError, match="显式授权"):
        reader.read_source(registered_project.id, relative)


@pytest.mark.parametrize(
    "relative",
    [
        "../outside.md",
        "docs/../../outside.md",
        "/fictional/outside.md",
        "C:/outside.md",
        "folder\\notes.md",
        " STATUS.md",
        "STATUS.md\n",
        "bad\x00.md",
        "notes.txt",
        ".env",
        ".env.md",
        ".ENV.local.md",
        "private.pem",
        "private.key",
        "archive.p12",
        "vault.keystore",
        "copy_id_rsa.md",
        "copy_id_ed25519.md",
        "service-credentials.md",
        ".ssh/config",
        ".ssh/config.md",
        "nested/.git/notes.md",
        ".DS_Store",
        ".aws/notes.md",
        ".gnupg/notes.md",
        "key.pem/notes.md",
    ],
)
def test_even_manually_whitelisted_unsafe_paths_are_denied(
    relative, reader, registered_project, monkeypatch
):
    set_whitelist(reader, registered_project.id, [relative])
    monkeypatch.setattr(os, "read", forbid_read)
    with pytest.raises(PolicyError):
        reader.read_source(registered_project.id, relative)


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("bom", [False, True])
def test_source_hash_and_size_use_raw_bytes(reader, registered_project, demo_project, ending, bom):
    normalized = "# 纯虚构\n中文内容\n末行"
    raw = (b"\xef\xbb\xbf" if bom else b"") + normalized.replace("\n", ending).encode()
    (demo_project / "STATUS.md").write_bytes(raw)
    snapshot = reader.read_source(registered_project.id, "STATUS.md")
    assert snapshot.text == normalized
    assert snapshot.sha256 == sha256(raw).hexdigest()
    assert snapshot.size_bytes == len(raw)
    assert snapshot.file_mtime_utc.endswith("Z")
    assert "中文内容" not in repr(snapshot)


def test_read_only_nofollow_descriptor_is_closed(reader, registered_project, monkeypatch):
    original_open = os.open
    descriptors = []

    def checked_open(path, flags, *args, **kwargs):
        assert flags & os.O_NOFOLLOW
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        if path == "STATUS.md":
            assert kwargs["dir_fd"] >= 0
            assert flags & os.O_NONBLOCK
        descriptor = original_open(path, flags, *args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", checked_open)
    reader.read_source(registered_project.id, "STATUS.md")
    assert descriptors
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("outside", [False, True], ids=["inside-root", "outside-root"])
def test_final_symlink_added_after_registration_is_denied(
    reader, registered_project, demo_project, tmp_path, monkeypatch, outside
):
    target = tmp_path / "fictional-target.md" if outside else demo_project / "STATUS_CRLF.md"
    if outside:
        target.write_text("纯虚构，禁止通过链接读取", encoding="utf-8")
    source = demo_project / "STATUS.md"
    source.unlink()
    source.symlink_to(target)
    monkeypatch.setattr(os, "read", forbid_read)
    with pytest.raises(PolicyError):
        reader.read_source(registered_project.id, "STATUS.md")


def test_root_replaced_with_symlink_is_denied(reader, registered_project, demo_project, tmp_path):
    moved = tmp_path / "fictional-moved-root"
    demo_project.rename(moved)
    demo_project.symlink_to(moved, target_is_directory=True)
    with pytest.raises(PolicyError, match="根路径"):
        reader.read_source(registered_project.id, "STATUS.md")


def test_dangling_final_symlink_is_policy_denied_not_a_missing_source(
    reader, registered_project, demo_project, monkeypatch
):
    source = demo_project / "STATUS.md"
    source.unlink()
    source.symlink_to(demo_project / "fictional-missing-target.md")
    monkeypatch.setattr(os, "read", forbid_read)
    with pytest.raises(PolicyError, match="悬空"):
        reader.read_source(registered_project.id, "STATUS.md")


def test_in_root_parent_alias_is_resolved_safely(demo_project):
    target = demo_project / "notes"
    target.mkdir()
    (target / "state.markdown").write_text("# 虚构\n明确授权的文档", encoding="utf-8")
    (demo_project / "alias").symlink_to(target, target_is_directory=True)
    settings = load_settings()
    project = ProjectRegistry(settings).add("alias", demo_project, ["alias/state.markdown"])
    with database(settings.data_dir) as connection:
        snapshot = SourcePolicy(connection, settings).read_source(
            project.id, "alias/state.markdown"
        )
    assert snapshot.relative_path == "alias/state.markdown"
    assert snapshot.text == "# 虚构\n明确授权的文档"


def test_parent_alias_cannot_escape_after_registration(demo_project, tmp_path, monkeypatch):
    folder = demo_project / "notes"
    folder.mkdir()
    (folder / "state.md").write_text("虚构已授权内容", encoding="utf-8")
    settings = load_settings()
    project = ProjectRegistry(settings).add("nested", demo_project, ["notes/state.md"])
    folder.rename(tmp_path / "fictional-outside")
    folder.symlink_to(tmp_path / "fictional-outside", target_is_directory=True)
    with database(settings.data_dir) as connection:
        monkeypatch.setattr(os, "read", forbid_read)
        with pytest.raises(PolicyError):
            SourcePolicy(connection, settings).read_source(project.id, "notes/state.md")


def test_alias_to_a_denied_component_is_denied(
    reader, registered_project, demo_project, monkeypatch
):
    folder = demo_project / ".env-fixture"
    folder.mkdir()
    (folder / "state.md").write_text("纯虚构假值", encoding="utf-8")
    (demo_project / "alias").symlink_to(folder, target_is_directory=True)
    set_whitelist(reader, registered_project.id, ["alias/state.md"])
    monkeypatch.setattr(os, "read", forbid_read)
    with pytest.raises(PolicyError):
        reader.read_source(registered_project.id, "alias/state.md")


@pytest.mark.parametrize("kind", ["directory", "fifo", "hardlink"])
def test_only_independent_regular_files_are_read(
    kind, reader, registered_project, demo_project, tmp_path, monkeypatch
):
    source = demo_project / "STATUS.md"
    if kind == "hardlink":
        os.link(source, tmp_path / "fictional-hardlink.md")
    else:
        source.unlink()
        if kind == "directory":
            source.mkdir()
        else:
            os.mkfifo(source)
    monkeypatch.setattr(os, "read", forbid_read)
    with pytest.raises(PolicyError):
        reader.read_source(registered_project.id, "STATUS.md")


@pytest.mark.parametrize("size", [MAX_FILE_BYTES, MAX_FILE_BYTES + 1])
def test_raw_byte_size_limit(reader, registered_project, demo_project, size):
    (demo_project / "STATUS.md").write_bytes(b"x" * size)
    if size == MAX_FILE_BYTES:
        assert reader.read_source(registered_project.id, "STATUS.md").size_bytes == size
    else:
        with pytest.raises(PolicyError, match="1 MiB"):
            reader.read_source(registered_project.id, "STATUS.md")


@pytest.mark.parametrize("raw", [b"\xff", b"# fictional\x00binary"])
def test_invalid_text_is_denied_on_read(reader, registered_project, demo_project, raw):
    (demo_project / "STATUS.md").write_bytes(raw)
    with pytest.raises(PolicyError):
        reader.read_source(registered_project.id, "STATUS.md")


@pytest.mark.parametrize("growth", [False, True], ids=["rewrite", "over-limit"])
def test_change_during_read_fails_closed(
    reader, registered_project, demo_project, monkeypatch, growth
):
    original_read = os.read
    changed = False

    def read_and_change(descriptor, size):
        nonlocal changed
        value = original_read(descriptor, size)
        if not changed:
            changed = True
            (demo_project / "STATUS.md").write_bytes(
                b"x" * (MAX_FILE_BYTES + 1) if growth else b"fictional changed"
            )
        return value

    monkeypatch.setattr(os, "read", read_and_change)
    with pytest.raises(WorkPilotError) as caught:
        reader.read_source(registered_project.id, "STATUS.md")
    assert caught.value.code == (ExitCode.DENIED if growth else ExitCode.BUSY)
    assert "CANARY-DO-NOT-LEAK" not in str(caught.value)


def test_final_swap_at_open_cannot_follow_symlink(
    reader, registered_project, demo_project, tmp_path, monkeypatch
):
    outside = tmp_path / "fictional-outside.md"
    outside.write_text("CANARY-DO-NOT-LEAK", encoding="utf-8")
    original_open = os.open

    def swap_then_open(path, flags, *args, **kwargs):
        if path == "STATUS.md":
            source = demo_project / "STATUS.md"
            source.unlink()
            source.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_then_open)
    monkeypatch.setattr(os, "read", forbid_read)
    with pytest.raises(PolicyError):
        reader.read_source(registered_project.id, "STATUS.md")


@pytest.mark.parametrize("when", ["before-parent-open", "after-parent-open"])
def test_parent_swap_cannot_read_outside_the_pinned_directory(
    demo_project, tmp_path, monkeypatch, when
):
    folder = demo_project / "notes"
    folder.mkdir()
    (folder / "state.md").write_text("虚构已授权正文", encoding="utf-8")
    outside = tmp_path / "fictional-outside"
    outside.mkdir()
    (outside / "state.md").write_text("OUTSIDE-CANARY-NEVER-READ", encoding="utf-8")
    settings = load_settings()
    project = ProjectRegistry(settings).add("nested", demo_project, ["notes/state.md"])
    original_open, original_read = os.open, os.read
    opened_bytes = []
    trigger = "notes" if when == "before-parent-open" else "state.md"

    def swap_then_open(path, flags, *args, **kwargs):
        if path == trigger:
            folder.rename(demo_project / "moved")
            folder.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    def checked_read(descriptor, size):
        value = original_read(descriptor, size)
        opened_bytes.append(value)
        return value

    with database(settings.data_dir) as connection:
        monkeypatch.setattr(os, "open", swap_then_open)
        monkeypatch.setattr(os, "read", checked_read)
        with pytest.raises(PolicyError):
            SourcePolicy(connection, settings).read_source(project.id, "notes/state.md")
    assert b"OUTSIDE-CANARY-NEVER-READ" not in b"".join(opened_bytes)


def test_revoked_while_reading_cannot_return_a_snapshot(reader, registered_project, monkeypatch):
    original_read = os.read

    def read_then_revoke(descriptor, size):
        value = original_read(descriptor, size)
        set_whitelist(reader, registered_project.id, ["STATUS_CRLF.md"])
        return value

    monkeypatch.setattr(os, "read", read_then_revoke)
    with pytest.raises(PolicyError, match="显式授权"):
        reader.read_source(registered_project.id, "STATUS.md")


def test_runtime_directory_cannot_overlap_source(reader, registered_project, demo_project):
    reader.settings = reader.settings.model_copy(update={"data_dir": demo_project / "runtime"})
    with pytest.raises(PolicyError, match="分开"):
        reader.read_source(registered_project.id, "STATUS.md")


def test_os_errors_never_echo_original_paths_or_source_content(
    reader, registered_project, monkeypatch
):
    def fail_read(*_args):
        raise OSError("CANARY-DO-NOT-LEAK /fictional/private/path")

    monkeypatch.setattr(os, "read", fail_read)
    with pytest.raises(PolicyError) as caught:
        reader.read_source(registered_project.id, "STATUS.md")
    assert "CANARY" not in str(caught.value)
    assert "/fictional" not in str(caught.value)
