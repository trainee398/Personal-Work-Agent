"""T04 tool contracts, fixed preview scope, and fresh authorization on every read."""

import json
from hashlib import sha256

import pytest

from workpilot.agent.context import prepare_context, render_preview
from workpilot.config import EXCERPT_MAX_CHARS, load_settings
from workpilot.errors import NotFoundError, PolicyError, WorkPilotError
from workpilot.llm.client import json_text
from workpilot.projects import ProjectRegistry
from workpilot.storage.db import database
from workpilot.tools.project_tools import ProjectTools, tool_definitions


def invoke(context, name, **arguments):
    return ProjectTools(context, load_settings()).invoke(
        name, json_text({"project_id": context.project.id, **arguments})
    )


def test_only_three_schema_scoped_read_tools_exist():
    definitions = {tool.name: tool for tool in tool_definitions()}
    assert set(definitions) == {
        "get_project_overview",
        "search_project_sources",
        "list_project_memories",
    }
    for definition in definitions.values():
        assert definition.parameters["additionalProperties"] is False
        assert "project_id" in definition.parameters["required"]
        assert not {"path", "root", "command", "url"} & definition.parameters["properties"].keys()


def test_overview_contract_and_true_heading_levels(agent_context):
    result = invoke(agent_context, "get_project_overview")
    assert "上次同步" in result["notice"]
    assert result["sources"] == [
        {
            "relative_path": "STATUS.md",
            "headings": ["虚构项目", "风险", "下一步"],
            "line_count": 5,
            "indexed_at": agent_context.sources[0].indexed_at,
            "sha": agent_context.sources[0].sha256[:8],
            "stale": False,
        }
    ]
    assert str(agent_context.project.root) not in json_text(result)


def test_search_has_exact_visible_ranges_and_bounded_excerpt(agent_context):
    tools = ProjectTools(agent_context, load_settings())
    result = tools.invoke(
        "search_project_sources",
        json_text({"project_id": agent_context.project.id, "query": "风险"}),
    )
    assert len(result["chunks"]) == 1
    hit = result["chunks"][0]
    assert set(hit) == {"relative_path", "line_start", "line_end", "heading", "excerpt"}
    assert (hit["line_start"], hit["line_end"]) == (2, 3)
    assert len(hit["excerpt"]) <= EXCERPT_MAX_CHARS
    assert "测试书目数量不足" in hit["excerpt"]
    assert tools.delivered == {agent_context.evidence[0].chunk_id}


@pytest.mark.parametrize("query", ["", "没有命中", "下一步", "../../outside.md"])
def test_tools_cannot_expand_the_dry_run_selection(agent_context, query):
    assert invoke(agent_context, "search_project_sources", query=query)["chunks"] == []


@pytest.mark.parametrize(
    "kind, count", [(None, 1), ("decision", 1), ("note", 0), ("preference", 0)]
)
def test_manual_memory_is_labelled_and_filterable(agent_context, kind, count):
    result = invoke(agent_context, "list_project_memories", kind=kind)
    assert result["notice"] == "人工记忆，不是项目进度证据"
    assert len(result["memories"]) == count
    if count:
        assert set(result["memories"][0]) == {"kind", "body", "created_at"}
        assert "人工决定" in result["memories"][0]["body"]


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("shell", {"command": "delete everything"}),
        ("read_file", {"path": "../outside.md"}),
        ("memory_add", {"body": "模型猜测"}),
        ("get_project_overview", {"path": ".env"}),
        ("get_project_overview", {"project_id": "another-project"}),
        ("search_project_sources", {"query": 123}),
        ("list_project_memories", {"kind": "inferred"}),
    ],
)
def test_untrusted_tool_calls_cannot_gain_capabilities_or_cross_projects(
    agent_context, name, arguments
):
    supplied = {"project_id": agent_context.project.id, **arguments}
    with pytest.raises(PolicyError):
        ProjectTools(agent_context, load_settings()).invoke(name, json_text(supplied))


@pytest.mark.parametrize("arguments", ["{", "null", "[]", '{"project_id":NaN}'])
def test_invalid_json_is_a_safe_denial(agent_context, arguments):
    with pytest.raises(PolicyError, match="参数无效"):
        ProjectTools(agent_context, load_settings()).invoke("get_project_overview", arguments)


def test_project_removed_after_preview_is_rejected(agent_context):
    ProjectRegistry(load_settings()).remove("demo")
    with pytest.raises(NotFoundError):
        invoke(agent_context, "get_project_overview")


def test_whitelist_revoked_after_preview_is_rechecked(agent_context):
    with database(load_settings().data_dir) as connection:
        connection.execute(
            "UPDATE projects SET allowed_files_json = ?", (json.dumps(["STATUS_CRLF.md"]),)
        )
    with pytest.raises(PolicyError, match="授权已撤销"):
        invoke(agent_context, "search_project_sources", query="风险")


def test_source_change_after_preview_is_rejected_before_returning_cached_text(
    agent_context, demo_project
):
    (demo_project / "STATUS.md").write_text("已变更的虚构内容", encoding="utf-8")
    with pytest.raises(WorkPilotError) as caught:
        invoke(agent_context, "get_project_overview")
    assert caught.value.code == 7


def test_removed_memory_cannot_be_sent_from_an_old_preview(agent_context):
    with database(load_settings().data_dir) as connection:
        connection.execute("DELETE FROM memory_entries")
    with pytest.raises(WorkPilotError, match="人工记忆已变化"):
        invoke(agent_context, "list_project_memories")


def test_dry_run_has_only_metadata_and_tools_do_not_change_sources(agent_context, demo_project):
    before = {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in demo_project.iterdir()
        if path.is_file()
    }
    preview = render_preview(agent_context)
    assert "STATUS.md | L2-L3" in preview
    assert "人工记忆/decision" in preview and "估算 tokens" in preview
    for private in (agent_context.question, "测试书目数量不足", "人工决定", "虚构项目"):
        assert private not in preview
    for name in ("get_project_overview", "list_project_memories"):
        invoke(agent_context, name)
    invoke(agent_context, "search_project_sources", query="风险")
    assert {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in demo_project.iterdir()
        if path.is_file()
    } == before
    with database(load_settings().data_dir) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0] == 3


def test_missing_source_is_excluded_before_preview(agent_context, demo_project):
    (demo_project / "STATUS.md").unlink()
    context = prepare_context("demo", "风险", load_settings())
    assert not context.sources and not context.evidence
    assert "来源缺失" in render_preview(context)


def test_headings_in_code_fences_and_third_level_are_not_reported(agent_context, demo_project):
    from workpilot.ingestion.sync import sync_project

    (demo_project / "STATUS.md").write_text(
        "# 一\n## 二\n### 三\n风险内容\n```md\n## 不是标题\n```\n", encoding="utf-8"
    )
    sync_project("demo", load_settings())
    context = prepare_context("demo", "风险", load_settings())
    assert invoke(context, "get_project_overview")["sources"][0]["headings"] == ["一", "二"]
