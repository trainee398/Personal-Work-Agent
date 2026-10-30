"""A persistent lock inode serializes sync across projects and processes."""

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from workpilot.config import DATABASE_MODE, LOCK_NAME
from workpilot.errors import ExitCode, WorkPilotError


@contextmanager
def sync_lock(data_dir: Path) -> Iterator[None]:
    descriptor = None
    acquired = False
    try:
        try:
            descriptor = os.open(
                data_dir / LOCK_NAME,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                DATABASE_MODE,
            )
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
                raise WorkPilotError("同步锁必须是当前用户的独立普通文件。")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WorkPilotError("已有同步正在运行，未等待锁。", ExitCode.BUSY) from None
            acquired = True
            os.fchmod(descriptor, DATABASE_MODE)
        except OSError:
            raise WorkPilotError("无法安全访问同步锁，请检查数据目录和权限。") from None
        yield
    finally:
        if descriptor is not None:
            try:
                if acquired:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
