"""T04: deterministic offline model/tool rounds, budgets, and invalid-answer paths."""

import asyncio
import json
from dataclasses import replace
from hashlib import sha256

import pytest

from workpilot.agent import runtime
from workpilot.agent.context import prepare_context
from workpilot.config import MAX_CONTEXT_CHARS, MAX_OUTPUT_TOKENS, MAX_TOOL_ROUNDS, load_settings
from workpilot.errors import PolicyError, WorkPilotError
from workpilot.llm.client import ModelResponse, StubModel, ToolCall, json_text
from workpilot.storage.db import database

ANSWER = "测试书目数量不足，可能影响演示。[STATUS.md:L2-L3]"


def tool_reply(context, identifier="call-1", name="search_project_sources", **arguments):
    values = {"project_id": context.project.id, **arguments}
    if name == "search_project_sources":
        values.setdefault("query", "风险")
    return ModelResponse(
        tool_calls=(ToolCall(identifier, name, json_text(values)),), tokens_in=10, tokens_out=2
    )


def run(context, model, **kwargs):
    return asyncio.run(runtime.run_agent(context, load_settings(), model, **kwargs))


def test_stub_drives_a_real_read_tool_then_a_verified_answer(agent_context):
    model = StubModel(
        [tool_reply(agent_context), ModelResponse(ANSWER, tokens_in=20, tokens_out=5)]
    )
    result = run(agent_context, model)
    assert ANSWER in result.text and "引用来源：" in result.text
    assert (result.tokens_in, result.tokens_out, result.tool_rounds) == (30, 7, 1)
    assert len(model.requests) == 2 and model.closed
    returned = json.loads(model.requests[1].messages[-1].content)
    assert "测试书目数量不足" in returned["chunks"][0]["excerpt"]
    assert all(request.max_output_tokens == MAX_OUTPUT_TOKENS for request in model.requests)
    assert all(request.context_chars <= MAX_CONTEXT_CHARS for request in model.requests)


def test_all_three_tools_and_manual_evidence_distinction(agent_context):
    model = StubModel(
        [
            tool_reply(agent_context, "overview", "get_project_overview"),
            tool_reply(agent_context, "search"),
            tool_reply(agent_context, "memories", "list_project_memories", kind="decision"),
            ModelResponse(ANSWER),
        ]
    )
    result = run(agent_context, model)
    assert result.tool_rounds == 3
    assert "人工记忆，不是项目进度证据" in result.text and "人工决定" in result.text
    assert (
        result.text.index("文件证据")
        < result.text.index("人工记忆")
        < result.text.index("引用来源")
    )


def test_multiple_tool_calls_are_executed_sequentially_with_matching_ids(agent_context):
    first = tool_reply(agent_context, "search").tool_calls[0]
    second = tool_reply(agent_context, "overview", "get_project_overview").tool_calls[0]
    model = StubModel([ModelResponse(tool_calls=(first, second)), ModelResponse(ANSWER)])
    assert ANSWER in run(agent_context, model).text
    assert [m.call_id for m in model.requests[1].messages if m.role == "tool"] == [
        "search",
        "overview",
    ]


def test_no_matching_evidence_does_not_call_a_model(agent_context):
    context = prepare_context("demo", "没有命中的关键词", load_settings())
    model = StubModel([])
    result = run(context, model)
    assert "现有索引不足" in result.text and not model.requests and model.closed


@pytest.mark.parametrize("answer", ["无引用的猜测。", "错误事实。[invented.md:L1-L2]", ANSWER])
def test_final_answer_without_tool_evidence_is_withheld(agent_context, answer):
    result = run(agent_context, StubModel([ModelResponse(answer)]))
    assert "现有索引不足" in result.text and answer not in result.text


def test_six_tool_rounds_allow_one_final_turn_without_tools(agent_context):
    model = StubModel(
        [
            *(tool_reply(agent_context, f"call-{i}") for i in range(MAX_TOOL_ROUNDS)),
            ModelResponse(ANSWER),
        ]
    )
    result = run(agent_context, model)
    assert ANSWER in result.text and result.tool_rounds == MAX_TOOL_ROUNDS
    assert len(model.requests) == MAX_TOOL_ROUNDS + 1
    assert model.requests[-1].tools == ()
    assert "工具预算已用尽" in model.requests[-1].messages[-1].content


def test_model_cannot_request_a_seventh_tool_round(agent_context):
    model = StubModel([tool_reply(agent_context, f"call-{i}") for i in range(MAX_TOOL_ROUNDS + 1)])
    result = run(agent_context, model)
    assert result.tool_rounds == MAX_TOOL_ROUNDS and "工具轮次预算已用尽" in result.text
    assert "现有索引不足" in result.text and model.closed


def test_oversized_tool_material_is_not_admitted_and_existing_evidence_can_finish(agent_context):
    settings = load_settings()
    with database(settings.data_dir) as connection:
        connection.execute("UPDATE memory_entries SET body = ?", ("PRIVATE-MEMORY-" * 750,))
    context = prepare_context("demo", "风险", settings)
    model = StubModel(
        [
            tool_reply(context, "search"),
            tool_reply(context, "memory", "list_project_memories"),
            ModelResponse(ANSWER),
        ]
    )
    result = run(context, model)
    assert all(request.context_chars <= MAX_CONTEXT_CHARS for request in model.requests)
    assert "PRIVATE-MEMORY-" not in result.text
    assert "PRIVATE-MEMORY-" not in json_text([m.content for m in model.requests[-1].messages])
    assert ANSWER in result.text and not model.requests[-1].tools


def test_oversized_initial_material_is_rejected_before_model_use(agent_context):
    with database(load_settings().data_dir) as connection:
        connection.execute("UPDATE memory_entries SET body = ?", ("私" * MAX_CONTEXT_CHARS,))
    with pytest.raises(PolicyError, match="12000"):
        prepare_context("demo", "风险", load_settings())


def test_oversized_model_history_cannot_expand_the_next_request(agent_context):
    first = tool_reply(agent_context)
    model = StubModel([replace(first, text="MODEL-CANARY" * 1200)])
    result = run(agent_context, model)
    assert len(model.requests) == 1
    assert "MODEL-CANARY" not in result.text and "预算" in result.text


@pytest.mark.parametrize("error", [RuntimeError("ERROR-CANARY"), OSError("PRIVATE-KEY")])
def test_model_failures_are_safe_and_client_is_closed(agent_context, error):
    model = StubModel([error])
    with pytest.raises(WorkPilotError) as caught:
        run(agent_context, model)
    assert (
        caught.value.code == 5
        and "CANARY" not in str(caught.value)
        and "PRIVATE" not in str(caught.value)
    )
    assert model.closed


def test_per_model_timeout_cancels_the_stub_and_closes_it(agent_context, monkeypatch):
    async def slow(_request):
        await asyncio.sleep(1)
        return ModelResponse(ANSWER)

    monkeypatch.setattr(runtime, "MODEL_TIMEOUT_S", 0.01)
    model = StubModel([slow])
    with pytest.raises(WorkPilotError) as caught:
        run(agent_context, model, total_timeout_s=0.5)
    assert caught.value.code == 5 and "超时" in str(caught.value)
    assert model.closed


def test_total_timeout_is_shared_across_model_rounds(agent_context):
    async def step(_request):
        await asyncio.sleep(0.02)
        return tool_reply(agent_context, f"call-{len(model.requests)}")

    model = StubModel([step, step, ModelResponse(ANSWER)])
    with pytest.raises(WorkPilotError) as caught:
        run(agent_context, model, total_timeout_s=0.03)
    assert caught.value.code == 5 and len(model.requests) <= 2 and model.closed


def test_output_budget_and_reused_tool_ids_are_rejected(agent_context):
    for script in (
        [ModelResponse("PRIVATE-CANARY", tokens_out=MAX_OUTPUT_TOKENS + 1)],
        [tool_reply(agent_context), tool_reply(agent_context)],
    ):
        model = StubModel(script)
        with pytest.raises(WorkPilotError) as caught:
            run(agent_context, model)
        assert caught.value.code == 5 and "PRIVATE-CANARY" not in str(caught.value)
        assert model.closed


def test_revocation_before_next_step_prevents_resending_prior_material(agent_context, monkeypatch):
    original = runtime.ProjectTools.invoke

    def invoke_and_revoke(self, *args):
        result = original(self, *args)
        with database(load_settings().data_dir) as connection:
            connection.execute("UPDATE projects SET allowed_files_json = '[\"STATUS_CRLF.md\"]'")
        return result

    monkeypatch.setattr(runtime.ProjectTools, "invoke", invoke_and_revoke)
    model = StubModel([tool_reply(agent_context), ModelResponse(ANSWER)])
    with pytest.raises(PolicyError):
        run(agent_context, model)
    assert len(model.requests) == 1 and model.closed


def test_source_deleted_during_final_model_step_fails_closed(agent_context, demo_project):
    def final(_request):
        (demo_project / "STATUS.md").unlink()
        return ModelResponse(ANSWER)

    result = run(agent_context, StubModel([tool_reply(agent_context), final]))
    assert "现有索引不足" in result.text and ANSWER not in result.text


def test_prompt_injection_cannot_create_a_write_tool(agent_context, demo_project):
    path = demo_project / "STATUS.md"
    before = sha256(path.read_bytes()).hexdigest()
    model = StubModel([tool_reply(agent_context, "attack", "shell", command="delete everything")])
    with pytest.raises(PolicyError, match="三个只读工具"):
        run(agent_context, model)
    assert sha256(path.read_bytes()).hexdigest() == before
    assert len(model.requests[0].tools) == 3 and model.closed


def test_budget_exhaustion_skips_remaining_tools_in_the_same_batch(agent_context, monkeypatch):
    with database(load_settings().data_dir) as connection:
        connection.execute("UPDATE memory_entries SET body = ?", ("PRIVATE-MEMORY-" * 750,))
    context = prepare_context("demo", "风险", load_settings())
    invoked = []
    original = runtime.ProjectTools.invoke

    def track(self, name, arguments):
        invoked.append(name)
        return original(self, name, arguments)

    monkeypatch.setattr(runtime.ProjectTools, "invoke", track)
    batch = ModelResponse(
        tool_calls=(
            tool_reply(context, "memory", "list_project_memories").tool_calls[0],
            tool_reply(context, "after-budget", "get_project_overview").tool_calls[0],
        )
    )
    model = StubModel([tool_reply(context, "search"), batch, ModelResponse(ANSWER)])
    result = run(context, model)
    assert ANSWER in result.text
    assert invoked == ["search_project_sources", "list_project_memories"]
    assert all("PRIVATE-MEMORY-" not in message.content for message in model.requests[-1].messages)


def test_provider_workpilot_errors_are_also_redacted(agent_context):
    model = StubModel([WorkPilotError("PRIVATE-CANARY", code=5)])
    with pytest.raises(WorkPilotError) as caught:
        run(agent_context, model)
    assert caught.value.code == 5 and "PRIVATE-CANARY" not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse(text=None),
        ModelResponse(tokens_in="PRIVATE-CANARY"),
        ModelResponse(tokens_out=True),
        ModelResponse(tool_calls=(ToolCall(1, "bad", "{}"),)),
    ],
)
def test_malformed_model_protocol_response_is_safe(agent_context, response):
    with pytest.raises(WorkPilotError) as caught:
        run(agent_context, StubModel([response]))
    assert caught.value.code == 5 and "PRIVATE-CANARY" not in str(caught.value)


def test_client_cleanup_errors_do_not_leak_or_replace_the_safe_answer(agent_context):
    class BrokenClose(StubModel):
        async def aclose(self):
            raise RuntimeError("CLOSE-CANARY")

    result = run(agent_context, BrokenClose([tool_reply(agent_context), ModelResponse(ANSWER)]))
    assert ANSWER in result.text and "CLOSE-CANARY" not in result.text
