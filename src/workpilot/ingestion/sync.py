"""Only explicitly registered files are read; each source replacement is atomic."""

from contextlib import nullcontext
from dataclasses import dataclass, field

from workpilot.config import Settings
from workpilot.errors import ExitCode, NotFoundError, PolicyError, WorkPilotError
from workpilot.ingestion.markdown import chunk_markdown
from workpilot.security.audit import AuditError, AuditRun
from workpilot.security.policy import SourcePolicy, relative_source_path
from workpilot.storage.db import database
from workpilot.storage.locking import sync_lock
from workpilot.storage.repositories import ProjectRepository, SourceRepository


@dataclass(frozen=True)
class SyncIssue:
    relative_path: str
    message: str
    code: int = 0


@dataclass
class SyncReport:
    project_name: str
    indexed: int = 0
    unchanged: int = 0
    invalidated: int = 0
    issues: list[SyncIssue] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        codes = {issue.code for issue in self.issues}
        for code in (ExitCode.BUSY, ExitCode.CONFIGURATION, ExitCode.DENIED, ExitCode.NOT_FOUND):
            if code in codes:
                return int(code)
        return 0


def display_path(relative: str) -> str:
    try:
        return relative_source_path(relative).as_posix()
    except PolicyError:
        return "[路径被拒绝]"


def sync_project(name: str, settings: Settings, *, audit: AuditRun | None = None) -> SyncReport:
    with database(settings.data_dir) as connection:
        projects = ProjectRepository(connection)
        project = projects.get(name)
        with sync_lock(settings.data_dir):
            project = projects.get_by_id(project.id)
            sources = SourceRepository(connection)
            policy = SourcePolicy(connection, settings)
            report = SyncReport(project_name=project.name)
            for relative in sources.invalidate_revoked(project.id):
                report.invalidated += 1
                report.issues.append(
                    SyncIssue(display_path(relative), "授权已移除，旧切片已删除。")
                )
            for relative in sorted(project.allowed_files):
                try:
                    scope = (
                        audit.tool("read_source", relative_path=relative)
                        if audit
                        else nullcontext()
                    )
                    with scope:
                        snapshot = policy.read_source(project.id, relative)
                        previous = sources.get(project.id, relative)
                        unchanged = (
                            previous is not None
                            and previous["active"]
                            and previous["sha256"] == snapshot.sha256
                        )
                        chunks = None if unchanged else chunk_markdown(snapshot.text)
                except AuditError:
                    raise
                except (NotFoundError, PolicyError) as error:
                    report.invalidated += int(sources.invalidate(project.id, relative))
                    report.issues.append(
                        SyncIssue(display_path(relative), str(error), int(error.code))
                    )
                    continue
                except WorkPilotError as error:
                    if error.code != ExitCode.BUSY:
                        raise
                    # An unstable read cannot leave previously cached text active.
                    report.invalidated += int(sources.invalidate(project.id, relative))
                    report.issues.append(
                        SyncIssue(display_path(relative), str(error), int(error.code))
                    )
                    break
                # Storage failures propagate after rollback. They must never be
                # mistaken for read failures and delete the previous evidence.
                if sources.save(project, snapshot, chunks):
                    report.indexed += 1
                else:
                    report.unchanged += 1
                if not snapshot.text:
                    report.issues.append(
                        SyncIssue(display_path(relative), "空文件，生成 0 个切片。")
                    )
            return report
