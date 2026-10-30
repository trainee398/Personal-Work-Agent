"""Atomic project registration CRUD; evidence and memories stay separate."""

import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from workpilot.domain.models import Project, ProjectRegistration
from workpilot.errors import ExitCode, NotFoundError, WorkPilotError
from workpilot.storage.db import transaction


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _project(row: sqlite3.Row) -> Project:
    try:
        return Project(
            id=row["id"],
            name=row["name"],
            root=row["root_path"],
            allowed_files=json.loads(row["allowed_files_json"]),
            active=row["active"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    except (ValueError, TypeError, ValidationError):
        raise WorkPilotError("项目登记数据无效，请检查本地数据库。") from None


class ProjectRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, name: str, *, include_removed: bool = False) -> Project:
        query = "SELECT * FROM projects WHERE name = ?"
        if not include_removed:
            query += " AND active = 1"
        row = self.connection.execute(query, (name,)).fetchone()
        if row is None:
            raise NotFoundError("未找到该项目。")
        return _project(row)

    def list(self, *, include_removed: bool = False) -> list[Project]:
        query = "SELECT * FROM projects"
        if not include_removed:
            query += " WHERE active = 1"
        rows = self.connection.execute(query + " ORDER BY name")
        return [_project(row) for row in rows]

    def add(self, registration: ProjectRegistration) -> Project:
        with transaction(self.connection):
            if self.connection.execute(
                "SELECT 1 FROM projects WHERE name = ?", (registration.name,)
            ).fetchone():
                raise WorkPilotError(
                    "项目名已存在；已移除项目也保留名称，彻底清除后才可重用。", ExitCode.USAGE
                )
            timestamp = utc_now()
            project = Project(
                **registration.model_dump(),
                id=str(uuid4()),
                created_at=timestamp,
                updated_at=timestamp,
            )
            self.connection.execute(
                """INSERT INTO projects (
                    id, name, root_path, allowed_files_json, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (
                    project.id,
                    project.name,
                    str(project.root),
                    json.dumps(project.allowed_files, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
        return project

    def remove(self, name: str) -> Project:
        with transaction(self.connection):
            project = self.get(name)
            self._delete_chunks(project.id)
            self.connection.execute(
                "UPDATE sources SET active = 0 WHERE project_id = ?", (project.id,)
            )
            self.connection.execute(
                "UPDATE projects SET active = 0, updated_at = ? WHERE id = ?",
                (utc_now(), project.id),
            )
        return project

    def purge(self, name: str, *, expected_id: str) -> None:
        """The CLI obtains confirmation before calling this destructive operation."""
        with transaction(self.connection):
            project = self.get(name, include_removed=True)
            if project.id != expected_id:
                raise WorkPilotError("项目登记已变化，请重新确认清除操作。", ExitCode.BUSY)
            self._delete_chunks(project.id)
            self.connection.execute("DELETE FROM sources WHERE project_id = ?", (project.id,))
            self.connection.execute(
                "DELETE FROM memory_entries WHERE project_id = ?", (project.id,)
            )
            # --purge removes registration, evidence and memories, not audit metadata.
            self.connection.execute(
                "UPDATE runs SET project_id = NULL WHERE project_id = ?", (project.id,)
            )
            self.connection.execute("DELETE FROM projects WHERE id = ?", (project.id,))

    def _delete_chunks(self, project_id: str) -> None:
        self.connection.execute(
            """DELETE FROM source_chunks WHERE source_id IN (
                SELECT id FROM sources WHERE project_id = ?
            )""",
            (project_id,),
        )
