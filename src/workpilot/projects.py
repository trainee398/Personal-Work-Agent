"""Project registration checks metadata only; source ingestion belongs to T02."""

import stat
from pathlib import Path, PureWindowsPath

from pydantic import ValidationError

from workpilot.config import ALLOWED_EXTENSIONS, Settings, has_control_characters
from workpilot.domain.models import Project, ProjectRegistration
from workpilot.errors import ExitCode, NotFoundError, PolicyError, WorkPilotError
from workpilot.security.policy import hard_denied as _hard_denied
from workpilot.storage.db import database
from workpilot.storage.repositories import ProjectRepository


def validate_registration(
    name: str, root: Path, files: list[str], settings: Settings
) -> ProjectRegistration:
    try:
        registration = ProjectRegistration(name=name, root=root, allowed_files=files)
    except ValidationError:
        raise WorkPilotError("项目名或授权文件清单无效。", ExitCode.USAGE) from None
    if ".." in root.parts or has_control_characters(str(root)) or _hard_denied(root.parts):
        raise PolicyError("项目根目录包含不允许的路径组件。")
    try:
        resolved_root = root.expanduser().resolve(strict=True)
        if not resolved_root.is_dir():
            raise WorkPilotError("项目根路径必须是目录。", ExitCode.USAGE)
        if _hard_denied(resolved_root.parts):
            raise PolicyError("项目根目录位于禁止访问的位置。")
        data_dir = settings.data_dir.resolve()
        if data_dir.is_relative_to(resolved_root) or resolved_root.is_relative_to(data_dir):
            raise PolicyError("源项目与 WorkPilot 运行数据目录必须分开存放。")
    except (FileNotFoundError, NotADirectoryError):
        raise WorkPilotError("项目根目录不存在。", ExitCode.USAGE) from None
    except (OSError, RuntimeError):
        raise PolicyError("无法安全解析项目根目录。") from None

    normalized = []
    for raw in registration.allowed_files:
        relative = Path(raw)
        if (
            not raw
            or raw != raw.strip()
            or relative.is_absolute()
            or PureWindowsPath(raw).drive
            or ".." in relative.parts
            or "\\" in raw
            or has_control_characters(raw)
            or _hard_denied(relative.parts)
            or relative.suffix.lower() not in ALLOWED_EXTENSIONS
        ):
            raise PolicyError(
                "授权文件必须是允许的相对 Markdown 路径，不能包含敏感文件或越界路径。"
            )
        candidate = resolved_root / relative
        try:
            if candidate.is_symlink():
                raise PolicyError("授权文件不能是符号链接。")
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(resolved_root) or _hard_denied(resolved.parts):
                raise PolicyError("授权文件解析后不在允许范围内。")
            if not stat.S_ISREG(resolved.stat().st_mode):
                raise PolicyError("授权路径必须指向普通 Markdown 文件。")
        except (FileNotFoundError, NotADirectoryError):
            raise NotFoundError("指定的授权文件不存在。") from None
        except (OSError, RuntimeError):
            raise PolicyError("无法安全检查授权文件。") from None
        normalized.append(relative.as_posix())

    return registration.model_copy(
        update={"root": resolved_root, "allowed_files": tuple(sorted(set(normalized)))}
    )


class ProjectRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def add(self, name: str, root: Path, files: list[str]) -> Project:
        registration = validate_registration(name, root, files, self.settings)
        with database(self.settings.data_dir) as connection:
            return ProjectRepository(connection).add(registration)

    def list(self) -> list[Project]:
        with database(self.settings.data_dir) as connection:
            return ProjectRepository(connection).list()

    def get(self, name: str, *, include_removed: bool = False) -> Project:
        with database(self.settings.data_dir) as connection:
            return ProjectRepository(connection).get(name, include_removed=include_removed)

    def remove(self, name: str) -> Project:
        with database(self.settings.data_dir) as connection:
            return ProjectRepository(connection).remove(name)

    def purge(self, name: str, *, expected_id: str) -> None:
        with database(self.settings.data_dir) as connection:
            ProjectRepository(connection).purge(name, expected_id=expected_id)
