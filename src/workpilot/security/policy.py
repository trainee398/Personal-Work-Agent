"""Authorize each read again and open only pinned, non-symlink file descriptors."""

import os
import sqlite3
import stat
from contextlib import ExitStack
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PureWindowsPath

from workpilot.config import ALLOWED_EXTENSIONS, MAX_FILE_BYTES, Settings, has_control_characters
from workpilot.domain.models import Project, SourceSnapshot
from workpilot.errors import ExitCode, NotFoundError, PolicyError, WorkPilotError
from workpilot.ingestion.markdown import normalize_markdown
from workpilot.storage.repositories import ProjectRepository


def hard_denied(parts: tuple[str, ...]) -> bool:
    """Shared with registration; callers can never relax this deny list."""
    for part in parts:
        value = part.lower()
        if (
            value in {".git", ".ssh", ".aws", ".gnupg", ".ds_store"}
            or value.startswith(".env")
            or value.endswith((".pem", ".key", ".p12", ".keystore"))
            or any(fragment in value for fragment in ("id_rsa", "id_ed25519", "credential"))
        ):
            return True
    return False


def relative_source_path(raw: str) -> Path:
    relative = Path(raw)
    if (
        not raw
        or raw != raw.strip()
        or relative.is_absolute()
        or PureWindowsPath(raw).drive
        or ".." in relative.parts
        or "\\" in raw
        or has_control_characters(raw)
        or hard_denied(relative.parts)
        or relative.suffix.lower() not in ALLOWED_EXTENSIONS
    ):
        raise PolicyError("源文件路径未通过相对路径、类型或硬拒绝清单校验。")
    return relative


def _signature(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _validate_file(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PolicyError("源路径必须是独立的普通文件，不能是链接或特殊文件。")
    if info.st_size > MAX_FILE_BYTES:
        raise PolicyError("源文件超过 1 MiB 上限。")


def _open_parent(path: Path, stack: ExitStack) -> int:
    """Pin every component of an already resolved absolute directory path."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    stack.callback(os.close, descriptor)
    for component in path.parts[1:]:
        descriptor = os.open(component, flags, dir_fd=descriptor)
        stack.callback(os.close, descriptor)
    return descriptor


def _changed() -> WorkPilotError:
    return WorkPilotError("源文件在读取期间发生变化，请重新同步。", ExitCode.BUSY)


class SourcePolicy:
    def __init__(self, connection: sqlite3.Connection, settings: Settings) -> None:
        self.projects = ProjectRepository(connection)
        self.settings = settings

    def _authorize(self, project_id: str, relative_path: str) -> Project:
        project = self.projects.get_by_id(project_id)
        if relative_path not in project.allowed_files:
            raise PolicyError("源文件未在项目的显式授权清单内。")
        relative_source_path(relative_path)
        if (
            not project.root.is_absolute()
            or ".." in project.root.parts
            or has_control_characters(str(project.root))
            or hard_denied(project.root.parts)
        ):
            raise PolicyError("登记的项目根路径不安全。")
        return project

    def read_source(self, project_id: str, relative_path: str) -> SourceSnapshot:
        """No arbitrary OS path parameter, recursion, network or source writes."""
        project = self._authorize(project_id, relative_path)
        candidate = project.root / relative_source_path(relative_path)
        try:
            root = project.root.resolve(strict=True)
            if root != project.root:
                raise PolicyError("登记后的项目根路径发生了符号链接变化。")
            data_dir = self.settings.data_dir.resolve()
            if root.is_relative_to(data_dir) or data_dir.is_relative_to(root):
                raise PolicyError("源项目与运行数据目录必须分开存放。")
            before_path = candidate.lstat()
            if stat.S_ISLNK(before_path.st_mode):
                raise PolicyError("授权来源不能是符号链接，包括悬空链接。")
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root) or hard_denied(resolved.parts):
                raise PolicyError("源文件 realpath 超出允许范围或命中硬拒绝清单。")
            _validate_file(before_path)
            with ExitStack() as stack:
                parent = _open_parent(resolved.parent, stack)
                descriptor = os.open(
                    resolved.name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent,
                )
                stack.callback(os.close, descriptor)
                before = os.fstat(descriptor)
                _validate_file(before)
                if _signature(before) != _signature(before_path):
                    raise _changed()
                pieces = []
                size = 0
                while True:
                    piece = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - size))
                    if not piece:
                        break
                    pieces.append(piece)
                    size += len(piece)
                    if size > MAX_FILE_BYTES:
                        raise PolicyError("读取期间源文件超过 1 MiB 上限。")
                after = os.fstat(descriptor)
                _validate_file(after)
                if _signature(before) != _signature(after) or size != after.st_size:
                    raise _changed()
                if candidate.resolve(strict=True) != resolved or project.root.resolve() != root:
                    raise PolicyError("读取期间源文件或项目路径的授权范围发生变化。")
                after_path = candidate.lstat()
                _validate_file(after_path)
                if _signature(after_path) != _signature(after):
                    raise _changed()
                current = self._authorize(project_id, relative_path)
                if current.root != project.root:
                    raise PolicyError("读取期间项目根目录授权发生变化。")
                raw = b"".join(pieces)
                return SourceSnapshot(
                    relative_path=relative_path,
                    text=normalize_markdown(raw),
                    sha256=sha256(raw).hexdigest(),
                    size_bytes=len(raw),
                    file_mtime_utc=datetime.fromtimestamp(
                        after.st_mtime_ns // 1_000_000_000, UTC
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
        except FileNotFoundError:
            raise NotFoundError("授权来源缺失。") from None
        except (OSError, RuntimeError, ValueError):
            raise PolicyError("无法安全读取授权来源，请检查路径、类型与读取权限。") from None
