"""Explicit manual notes stored independently of indexed project evidence."""

from workpilot.config import Settings
from workpilot.domain.models import MemoryEntry, MemoryKind
from workpilot.storage.db import database, read_snapshot
from workpilot.storage.repositories import MemoryRepository, ProjectRepository


class MemoryStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def add(self, name: str, kind: MemoryKind, text: str) -> MemoryEntry:
        with database(self.settings.data_dir) as connection:
            project = ProjectRepository(connection).get(name)
            return MemoryRepository(connection).add(project.id, kind, text)

    def list(self, name: str, kind: MemoryKind | None = None) -> list[MemoryEntry]:
        with database(self.settings.data_dir) as connection, read_snapshot(connection):
            project = ProjectRepository(connection).get(name)
            return MemoryRepository(connection).list(project.id, kind)
