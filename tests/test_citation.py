"""T04: facts must cite current, authorized evidence actually returned to this run."""

import os
from datetime import UTC, datetime, timedelta

import pytest

from workpilot.agent.citation import render_answer, verify_answer
from workpilot.agent.context import prepare_context
from workpilot.config import load_settings
from workpilot.ingestion.sync import sync_project
from workpilot.storage.db import database

VALID = "测试书目数量不足，可能影响演示。[STATUS.md:L2-L3]"


def verify(text, context):
    return verify_answer(text, context, load_settings(), {e.chunk_id for e in context.evidence})


def test_verified_fact_has_a_source_list_and_sync_timestamp(agent_context):
    result = verify(VALID, agent_context)
    assert not result.failed_closed and not result.invalid
    assert len(result.verified) == 1
    output = render_answer(result, agent_context, set())
    assert VALID in output
    assert "在上次同步时读取到" in output
    assert f"[STATUS.md:L2-L3] indexed_at={agent_context.sources[0].indexed_at}" in output


@pytest.mark.parametrize(
    "citation",
    [
        "[missing.md:L2-L3]",
        "[STATUS.md:L0-L3]",
        "[STATUS.md:L3-L2]",
        "[STATUS.md:L1-L5]",
        "[STATUS.md:L2-L999]",
        "[STATUS.md:L4-L5]",
        "[../outside.md:L2-L3]",
        "[/private/secret.md:L2-L3]",
        "[.env.md:L1-L2]",
        "[STATUS.md:L99999999999999999999-L99999999999999999999]",
        "[STATUS.md:Lx-Ly]",
        "[STATUS.md:2-3]",
    ],
)
def test_forged_or_out_of_scope_citation_fails_closed(agent_context, citation):
    text = "UNVERIFIED-CANARY 项目已经完成。" + citation
    result = verify(text, agent_context)
    assert result.failed_closed
    output = render_answer(result, agent_context, set())
    assert "UNVERIFIED-CANARY" not in output and "现有索引不足" in output
    assert "/private/secret.md" not in output


@pytest.mark.parametrize(
    "text",
    [
        "UNVERIFIED-CANARY 项目已经完成。",
        "未引用的事实。" + VALID,
        VALID + "未引用的结论。",
        VALID + "\n没有引用的下一步。",
        "## 项目已经全部完成\n" + VALID,
        '```json\n{"answer":"' + VALID + '"}\n```',
    ],
)
def test_uncited_sentences_or_internal_json_fail_closed(agent_context, text):
    result = verify(text, agent_context)
    assert result.failed_closed
    output = render_answer(result, agent_context, set())
    assert text not in output and "现有索引不足" in output


def test_mixed_valid_and_invalid_citations_are_explicitly_warned(agent_context):
    text = VALID + "\n尚需确认的说法。[nonexistent.md:L1-L2]"
    result = verify(text, agent_context)
    assert not result.failed_closed
    output = render_answer(result, agent_context, set())
    assert output.startswith("警示：存在未验证引用。")
    assert "[nonexistent.md:L1-L2]" in output
    assert len(result.verified) == 1


def test_existing_citation_without_tool_delivery_is_not_trusted(agent_context):
    result = verify_answer(VALID, agent_context, load_settings(), set())
    assert result.failed_closed and not result.verified


def test_repeated_citations_and_sentence_end_placements_are_supported(agent_context):
    text = "- 书目数量不足 [STATUS.md:L2-L3]。\n- 演示存在风险。[STATUS.md:L2-L3]"
    result = verify(text, agent_context)
    assert not result.failed_closed and len(result.verified) == 1


@pytest.mark.parametrize("mutation", ["inactive", "revoked", "deleted_chunk", "removed_project"])
def test_database_invalidation_after_model_call_prevents_old_claims(agent_context, mutation):
    statements = {
        "inactive": "UPDATE sources SET active = 0",
        "revoked": "UPDATE projects SET allowed_files_json = '[\"STATUS_CRLF.md\"]'",
        "deleted_chunk": "DELETE FROM source_chunks",
        "removed_project": "UPDATE projects SET active = 0",
    }
    with database(load_settings().data_dir) as connection:
        connection.execute(statements[mutation])
    assert verify(VALID, agent_context).failed_closed


def test_reindexed_same_line_numbers_are_not_the_old_evidence(agent_context, demo_project):
    (demo_project / "STATUS.md").write_text(
        "# 虚构项目\n## 风险\n另一份全新证据。\n", encoding="utf-8"
    )
    sync_project("demo", load_settings())
    assert verify(VALID, agent_context).failed_closed


@pytest.mark.parametrize("replacement", ["missing", "symlink"])
def test_unavailable_source_after_model_response_hides_its_claim(
    agent_context, demo_project, replacement
):
    path = demo_project / "STATUS.md"
    path.unlink()
    if replacement == "symlink":
        path.symlink_to(demo_project / "STATUS_CRLF.md")
    assert verify(VALID, agent_context).failed_closed


def test_stat_changes_while_final_answer_is_being_generated_are_warned(agent_context, demo_project):
    path = demo_project / "STATUS.md"
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000_000))
    result = verify(VALID, agent_context)
    assert not result.failed_closed
    assert result.stale == ("STATUS.md",)
    assert "索引可能过期，建议先 sync" in render_answer(result, agent_context, set())


def test_old_index_and_manual_memories_are_labelled_separately(agent_context):
    old = (datetime.now(UTC) - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with database(load_settings().data_dir) as connection:
        connection.execute("UPDATE sources SET indexed_at = ?", (old,))
    context = prepare_context("demo", "风险", load_settings())
    result = verify(VALID, context)
    output = render_answer(result, context, {entry.id for entry in context.memories})
    assert "索引可能过期" in output
    assert "人工记忆，不是项目进度证据" in output and "人工决定" in output
    assert output.index("文件证据") < output.index("人工记忆") < output.index("引用来源")


def test_truncated_excerpt_cannot_authorize_unseen_later_lines(agent_context, demo_project):
    text = "## 风险\n" + "文" * 500 + "\n另一个未发送的事实行。\n"
    (demo_project / "STATUS.md").write_text(text, encoding="utf-8")
    sync_project("demo", load_settings())
    context = prepare_context("demo", "风险", load_settings())
    assert context.evidence[0].line_end == 2
    assert verify("未发送的事实。[STATUS.md:L3-L3]", context).failed_closed


def test_english_fallback_and_footer(agent_context):
    from dataclasses import replace

    context = replace(agent_context, question="What is the risk?")
    result = verify("Unsupported assertion.", context)
    output = render_answer(result, context, set())
    assert "Insufficient indexed evidence" in output and "Citation sources:" in output
