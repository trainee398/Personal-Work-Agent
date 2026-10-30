"""Deterministic keyword search over active, currently authorized cached evidence."""

import heapq
import re
import sqlite3
import string
import unicodedata
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from workpilot.config import EXCERPT_MAX_CHARS, TOP_K_CHUNKS
from workpilot.errors import PolicyError
from workpilot.security.policy import hard_denied, relative_source_path
from workpilot.storage.repositories import ProjectRepository

ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
TERMS = re.compile(
    r"[a-z0-9]+|[\u3005\u3007\u3041-\u3096\u309d-\u309f\u30a1-\u30fa\u30fc-\u30ff"
    r"\u1100-\u11ff\u3131-\u318e\u31f0-\u31ff\ua960-\ua97c\ud7b0-\ud7fb"
    r"\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7a3\U00020000-\U000323af]+"
)


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).translate(ASCII_LOWER)
    result = []
    for match in TERMS.finditer(normalized):
        value = match[0]
        if value.isascii():
            result.append(value)
        else:
            result.extend(value[index : index + 2] for index in range(len(value) - 1))
    return result


def relevance_score(query: set[str], heading: str | None, body: str) -> int:
    title_terms = set(tokenize(heading or ""))
    body_terms = Counter(tokenize(body))
    return sum(3 * (term in title_terms) + min(body_terms[term], 3) for term in query)


@dataclass(frozen=True)
class SearchHit:
    relative_path: str
    line_start: int
    line_end: int
    heading: str | None
    excerpt: str
    indexed_at: str
    score: int


def search_sources(
    connection: sqlite3.Connection,
    project_id: str,
    query: str,
    *,
    limit: int = TOP_K_CHUNKS,
) -> list[SearchHit]:
    """Internal T02 API; no new CLI command or model tool is exposed."""
    with _read_snapshot(connection):
        return _search_sources(connection, project_id, query, limit=limit)


@contextmanager
def _read_snapshot(connection: sqlite3.Connection) -> Iterator[None]:
    # Authorization and chunks come from one coherent SQLite read snapshot.
    # A savepoint also respects a caller's existing transaction, without a write lock.
    connection.execute("SAVEPOINT workpilot_search")
    try:
        yield
    finally:
        connection.execute("RELEASE workpilot_search")


def _search_sources(
    connection: sqlite3.Connection, project_id: str, query: str, *, limit: int
) -> list[SearchHit]:
    project = ProjectRepository(connection).get_by_id(project_id)
    if not 1 <= limit <= TOP_K_CHUNKS:
        raise ValueError("Search limit must be between 1 and TOP_K_CHUNKS.")
    if hard_denied(project.root.parts):
        raise PolicyError("项目根路径命中硬拒绝清单。")
    query_terms = set(tokenize(query))
    if not query_terms:
        return []

    def hits():
        for relative in sorted(project.allowed_files):
            relative_source_path(relative)
            rows = connection.execute(
                """SELECT s.relative_path, s.indexed_at, c.line_start, c.line_end, c.heading, c.body
                FROM source_chunks c JOIN sources s ON s.id = c.source_id
                JOIN projects p ON p.id = s.project_id
                WHERE s.project_id = ? AND s.relative_path = ? AND s.active = 1 AND p.active = 1
                ORDER BY c.line_start""",
                (project_id, relative),
            )
            for row in rows:
                score = relevance_score(query_terms, row["heading"], row["body"])
                if not score:
                    continue
                body = row["body"]
                excerpt = (
                    body if len(body) <= EXCERPT_MAX_CHARS else body[: EXCERPT_MAX_CHARS - 1] + "…"
                )
                yield SearchHit(
                    relative_path=row["relative_path"],
                    line_start=row["line_start"],
                    line_end=row["line_end"],
                    heading=row["heading"],
                    excerpt=excerpt,
                    indexed_at=row["indexed_at"],
                    score=score,
                )

    return heapq.nsmallest(
        limit, hits(), key=lambda hit: (-hit.score, hit.relative_path, hit.line_start)
    )
