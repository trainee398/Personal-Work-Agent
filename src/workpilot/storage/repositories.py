"""Atomic project registration CRUD; evidence and memories stay separate."""

import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from workpilot.domain.models import (
    MemoryEntry, MemoryKind, Project, ProjectRegistration, SourceChunk, SourceSnapshot,
)
from workpilot.errors import ExitCode, NotFoundError, PolicyError, WorkPilotError
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

    def get_by_id(self, project_id: str) -> Project:
        row = self.connection.execute(
            "SELECT * FROM projects WHERE id = ? AND active = 1", (project_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("未找到仍在登记中的项目。")
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


class SourceRepository:
    """One transaction per source replacement, with authorization rechecked at commit."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, project_id: str, relative_path: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM sources WHERE project_id = ? AND relative_path = ?",
            (project_id, relative_path),
        ).fetchone()

    def list_metadata(self, project_id: str) -> list[sqlite3.Row]:
        """Cached line counts require no source file content reads."""
        return self.connection.execute(
            """SELECT s.*, COUNT(c.id) AS chunk_count, COALESCE(MAX(c.line_end), 0) AS line_count
            FROM sources s LEFT JOIN source_chunks c ON c.source_id = s.id
            WHERE s.project_id = ? GROUP BY s.id ORDER BY s.relative_path""",
            (project_id,),
        ).fetchall()

    def first_chunk(self, project_id: str, relative_path: str) -> sqlite3.Row | None:
        project = ProjectRepository(self.connection).get_by_id(project_id)
        if relative_path not in project.allowed_files:
            raise PolicyError("来源已撤销授权，不能展示旧正文。")
        return self.connection.execute(
            """SELECT c.* FROM source_chunks c JOIN sources s ON s.id = c.source_id
            WHERE s.project_id = ? AND s.relative_path = ? AND s.active = 1
            ORDER BY c.line_start LIMIT 1""",
            (project_id, relative_path),
        ).fetchone()

    def save(
        self, project: Project, snapshot: SourceSnapshot, chunks: list[SourceChunk] | None
    ) -> bool:
        with transaction(self.connection):
            current = ProjectRepository(self.connection).get_by_id(project.id)
            if current.root != project.root or snapshot.relative_path not in current.allowed_files:
                raise PolicyError("索引写入前项目或文件授权已变化。")
            previous = self.get(project.id, snapshot.relative_path)
            timestamp = utc_now()
            if (
                previous is not None
                and previous["active"]
                and previous["sha256"] == snapshot.sha256
            ):
                self.connection.execute(
                    """UPDATE sources SET size_bytes = ?, file_mtime_utc = ?, indexed_at = ?
                    WHERE id = ?""",
                    (snapshot.size_bytes, snapshot.file_mtime_utc, timestamp, previous["id"]),
                )
                return False
            if chunks is None:
                raise WorkPilotError("索引状态发生变化，请重新同步。", ExitCode.BUSY)
            source_id = previous["id"] if previous is not None else str(uuid4())
            if previous is None:
                self.connection.execute(
                    """INSERT INTO sources (
                        id, project_id, relative_path, sha256, size_bytes, file_mtime_utc,
                        indexed_at, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                    (
                        source_id,
                        project.id,
                        snapshot.relative_path,
                        snapshot.sha256,
                        snapshot.size_bytes,
                        snapshot.file_mtime_utc,
                        timestamp,
                    ),
                )
            else:
                self.connection.execute(
                    "DELETE FROM source_chunks WHERE source_id = ?", (source_id,)
                )
                self.connection.execute(
                    """UPDATE sources SET sha256 = ?, size_bytes = ?, file_mtime_utc = ?,
                    indexed_at = ?, active = 1 WHERE id = ?""",
                    (
                        snapshot.sha256,
                        snapshot.size_bytes,
                        snapshot.file_mtime_utc,
                        timestamp,
                        source_id,
                    ),
                )
            self.connection.executemany(
                """INSERT INTO source_chunks (
                    id, source_id, heading, line_start, line_end, body, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        str(uuid4()),
                        source_id,
                        chunk.heading,
                        chunk.line_start,
                        chunk.line_end,
                        chunk.body,
                        chunk.content_hash,
                    )
                    for chunk in chunks
                ],
            )
        return True

    def _invalidate(self, project_id: str, relative_path: str) -> bool:
        previous = self.get(project_id, relative_path)
        if previous is None:
            return False
        deleted = self.connection.execute(
            "DELETE FROM source_chunks WHERE source_id = ?", (previous["id"],)
        ).rowcount
        self.connection.execute("UPDATE sources SET active = 0 WHERE id = ?", (previous["id"],))
        return bool(previous["active"] or deleted)

    def invalidate(self, project_id: str, relative_path: str) -> bool:
        with transaction(self.connection):
            return self._invalidate(project_id, relative_path)

    def invalidate_revoked(self, project_id: str) -> list[str]:
        with transaction(self.connection):
            project = ProjectRepository(self.connection).get_by_id(project_id)
            rows = self.connection.execute(
                "SELECT relative_path FROM sources WHERE project_id = ? ORDER BY relative_path",
                (project_id,),
            ).fetchall()
            revoked = []
            for row in rows:
                relative = row["relative_path"]
                if relative not in project.allowed_files and self._invalidate(project_id, relative):
                    revoked.append(relative)
            return revoked


class MemoryRepository:
    """Explicit manual entries only; never copied into source evidence."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, project_id: str, kind: MemoryKind, body: str) -> MemoryEntry:
        with transaction(self.connection):
            ProjectRepository(self.connection).get_by_id(project_id)
            timestamp = utc_now()
            entry = MemoryEntry(
                id=str(uuid4()), project_id=project_id, kind=kind, body=body,
                created_at=timestamp, updated_at=timestamp,
            )
            self.connection.execute(
                """INSERT INTO memory_entries (
                    id, project_id, kind, body, origin, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'manual', ?, ?)""",
                (entry.id, project_id, kind.value, body, timestamp, timestamp),
            )
        return entry

    def list(self, project_id: str, kind: MemoryKind | None = None) -> list[MemoryEntry]:
        ProjectRepository(self.connection).get_by_id(project_id)
        query = "SELECT * FROM memory_entries WHERE project_id = ?"
        parameters = [project_id]
        if kind is not None:
            query += " AND kind = ?"
            parameters.append(kind.value)
        rows = self.connection.execute(query + " ORDER BY created_at, id", parameters)
        try:
            return [MemoryEntry(**dict(row)) for row in rows]
        except ValidationError:
            raise WorkPilotError("人工记忆数据无效，请检查本地数据库。") from None
