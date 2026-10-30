"""T02 / §§6.5, 7, 10: deterministic keyword evidence with precise provenance."""

import json
import sqlite3

import pytest

from workpilot.config import load_settings
from workpilot.errors import NotFoundError, PolicyError
from workpilot.ingestion.sync import sync_project
from workpilot.projects import ProjectRegistry
from workpilot.retrieval import relevance_score, search_sources, tokenize
from workpilot.storage.db import database, readonly_database


def index_documents(root, documents, name="search"):
    for relative, text in documents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    settings = load_settings()
    project = ProjectRegistry(settings).add(name, root, list(documents))
    assert sync_project(name, settings).exit_code == 0
    return project


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("ＡＰＩ WorkPilot_v2 １２", ["api", "workpilot", "v2", "12"]),
        ("当前主要风险", ["当前", "前主", "主要", "要风", "风险"]),
        ("中文，日文・カナ。한글", ["中文", "日文", "カナ", "한글"]),
        ("中 / 日 / 한", []),
        ("风险risk42风险", ["风险", "risk42", "风险"]),
        ("", []),
    ],
)
def test_nfkc_ascii_words_and_contiguous_cjk_bigrams(query, expected):
    assert tokenize(query) == expected


def test_scoring_uses_title_weight_and_caps_body_occurrences():
    assert relevance_score({"risk", "风险"}, "risk 风险", "risk " * 5 + "风险风险风险风险") == 12
    assert relevance_score({"task"}, "multitask", "multitasking") == 0
    assert relevance_score({"风险"}, None, "风险风险风险风险") == 3
    assert relevance_score({"risk"}, "risk risk risk", "") == 3


def test_chinese_title_match_ranks_above_repeated_body_hits(demo_project):
    project = index_documents(
        demo_project,
        {
            "body.md": "风险 风险 风险 风险\n",
            "title.md": "# 风险\n虚构正文\n",
        },
    )
    with database(load_settings().data_dir) as connection:
        hits = search_sources(connection, project.id, "风险")
        assert hits == search_sources(connection, project.id, "风险 风险")
    assert [(hit.relative_path, hit.score) for hit in hits] == [("title.md", 4), ("body.md", 3)]
    assert (hits[0].line_start, hits[0].line_end, hits[0].heading) == (1, 2, "风险")
    assert hits[0].indexed_at.endswith("Z")


def test_score_ties_sort_by_relative_path_then_starting_line(demo_project):
    project = index_documents(
        demo_project,
        {
            "b.md": "# 第二份\n同词\n",
            "a.md": "# 第一段\n同词\n# 第二段\n同词\n",
        },
    )
    with database(load_settings().data_dir) as connection:
        first = search_sources(connection, project.id, "同词")
        assert first == search_sources(connection, project.id, "同词")
    assert [(hit.relative_path, hit.line_start) for hit in first] == [
        ("a.md", 1),
        ("a.md", 3),
        ("b.md", 1),
    ]


def test_default_top_eight_and_smaller_requested_limit(demo_project):
    documents = {f"doc-{index:02}.md": "共同证据\n" for index in reversed(range(10))}
    project = index_documents(demo_project, documents)
    with database(load_settings().data_dir) as connection:
        default = search_sources(connection, project.id, "共同")
        assert [hit.relative_path for hit in default] == [
            f"doc-{index:02}.md" for index in range(8)
        ]
        assert search_sources(connection, project.id, "共同", limit=2) == default[:2]
        for invalid_limit in (0, 9):
            with pytest.raises(ValueError):
                search_sources(connection, project.id, "共同", limit=invalid_limit)


def test_excerpts_include_ellipsis_within_four_hundred_characters(demo_project):
    short = "风险 " + "文" * 397
    long = "风险 " + "文" * 500
    project = index_documents(demo_project, {"a.md": short, "b.md": long})
    with database(load_settings().data_dir) as connection:
        hits = search_sources(connection, project.id, "风险")
    assert hits[0].excerpt == short
    assert hits[1].excerpt == long[:399] + "…"
    assert [len(hit.excerpt) for hit in hits] == [400, 400]


@pytest.mark.parametrize("query", ["", "   ", "中", "没有命中", "absenttoken"])
def test_empty_queries_and_zero_matches_return_empty(demo_project, query):
    project = index_documents(demo_project, {"search.md": "唯一证据\n"})
    with database(load_settings().data_dir) as connection:
        assert search_sources(connection, project.id, query) == []


def test_no_index_is_an_empty_result_without_reading_files(registered_project):
    with database(load_settings().data_dir) as connection:
        assert search_sources(connection, registered_project.id, "风险") == []


def test_search_never_crosses_project_boundaries(demo_project):
    first = index_documents(demo_project, {"first.md": "firsttoken\n"}, "first")
    second = index_documents(demo_project, {"second.md": "secondtoken\n"}, "second")
    with database(load_settings().data_dir) as connection:
        assert search_sources(connection, first.id, "secondtoken") == []
        assert search_sources(connection, second.id, "firsttoken") == []
        assert len(search_sources(connection, second.id, "secondtoken")) == 1


def test_inactive_sources_are_excluded_even_if_old_chunks_remain(demo_project):
    project = index_documents(demo_project, {"search.md": "虚构证据\n"})
    with database(load_settings().data_dir) as connection:
        connection.execute("UPDATE sources SET active = 0 WHERE project_id = ?", (project.id,))
        assert connection.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0] == 1
        assert search_sources(connection, project.id, "虚构") == []


def test_removed_project_is_rejected_before_even_an_empty_query(demo_project):
    project = index_documents(demo_project, {"search.md": "虚构证据\n"})
    ProjectRegistry(load_settings()).remove(project.name)
    with database(load_settings().data_dir) as connection:
        with pytest.raises(NotFoundError):
            search_sources(connection, project.id, "")


def test_revoked_whitelist_excludes_cached_body_before_next_sync(demo_project):
    project = index_documents(demo_project, {"kept.md": "newtoken\n", "revoked.md": "oldtoken\n"})
    with database(load_settings().data_dir) as connection:
        connection.execute(
            "UPDATE projects SET allowed_files_json = ? WHERE id = ?",
            (json.dumps(["kept.md"]), project.id),
        )
        assert search_sources(connection, project.id, "oldtoken") == []
        assert len(search_sources(connection, project.id, "newtoken")) == 1


def test_hard_denied_whitelist_cannot_query_cached_evidence(demo_project):
    project = index_documents(demo_project, {"search.md": "虚构证据\n"})
    with database(load_settings().data_dir) as connection:
        connection.execute(
            "UPDATE projects SET allowed_files_json = ? WHERE id = ?",
            (json.dumps([".env.md"]), project.id),
        )
        with pytest.raises(PolicyError):
            search_sources(connection, project.id, "虚构")


def test_search_uses_one_consistent_read_snapshot_during_revocation(demo_project, monkeypatch):
    import workpilot.retrieval as retrieval

    project = index_documents(demo_project, {"a.md": "共同证据\n", "b.md": "共同证据\n"})
    settings = load_settings()
    original_score = retrieval.relevance_score
    revoked = False

    def score_and_revoke(*args):
        nonlocal revoked
        if not revoked:
            revoked = True
            writer = sqlite3.connect(settings.database_path, isolation_level=None, timeout=0)
            try:
                writer.execute(
                    "UPDATE projects SET allowed_files_json = ? WHERE id = ?",
                    (json.dumps(["a.md"]), project.id),
                )
            finally:
                writer.close()
        return original_score(*args)

    monkeypatch.setattr(retrieval, "relevance_score", score_and_revoke)
    with readonly_database(settings.data_dir) as connection:
        assert [hit.relative_path for hit in search_sources(connection, project.id, "共同")] == [
            "a.md",
            "b.md",
        ]
        assert [hit.relative_path for hit in search_sources(connection, project.id, "共同")] == [
            "a.md"
        ]
        assert not connection.in_transaction
