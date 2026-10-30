"""The real pinned SDK over an offline HTTP transport; no paid model calls."""

import asyncio
import json
import os
import subprocess
import sys

import httpx2
import pytest
from typer.testing import CliRunner

from workpilot.cli import app
from workpilot.errors import WorkPilotError
from workpilot.llm.client import Message, ModelRequest
from workpilot.llm.openai_client import SDK_VERSION, OpenAIModelClient, tracing_status
from workpilot.tools.project_tools import tool_definitions


@pytest.mark.parametrize("sdk_first", [False, True])
def test_tracing_is_closed_in_fresh_process_for_both_import_orders(sdk_first):
    script = """
import os, socket
def deny(*args, **kwargs):
    raise AssertionError("No DNS/TCP in tracing test")
socket.getaddrinfo = deny
socket.create_connection = deny
socket.socket.connect = deny
socket.socket.connect_ex = deny
os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "0"
if SDK_FIRST:
    import agents
import workpilot
from workpilot.llm.openai_client import tracing_status
version, disabled = tracing_status()
assert disabled and version == "0.24.0"
import agents
from agents.tracing.traces import NoOpTrace
with agents.trace("CANARY-DO-NOT-LEAK") as trace:
    assert isinstance(trace, NoOpTrace)
assert os.environ["OPENAI_AGENTS_DISABLE_TRACING"] == "1"
assert os.environ["OPENAI_AGENTS_DONT_LOG_MODEL_DATA"] == "1"
assert os.environ["OPENAI_AGENTS_DONT_LOG_TOOL_DATA"] == "1"
print("tracing disabled; zero network")
""".replace("SDK_FIRST", str(sdk_first))
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={"PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "tracing disabled; zero network"
    assert not result.stderr


def request(messages=None, tools=None):
    return ModelRequest(
        tuple(
            messages
            or [
                Message("system", "Offline test instruction."),
                Message("user", "Fictional question."),
            ]
        ),
        tool_definitions() if tools is None else tools,
        0.5,
        2000,
    )


def response_body(output):
    return {
        "id": "resp_fictional",
        "object": "response",
        "created_at": 1,
        "model": "fictional-model",
        "status": "completed",
        "output": output,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 3,
            "total_tokens": 13,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def test_sdk_uses_only_official_responses_with_exact_budgets_and_read_tools(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://must-not-be-used.invalid/v1")
    captured = []

    def handle(incoming):
        captured.append(incoming)
        return httpx2.Response(
            200,
            json=response_body(
                [
                    {
                        "type": "function_call",
                        "id": "fc_fictional",
                        "call_id": "call_fictional",
                        "name": "search_project_sources",
                        "arguments": '{"project_id":"demo","query":"风险"}',
                        "status": "completed",
                    }
                ]
            ),
        )

    async def scenario():
        transport = httpx2.AsyncClient(transport=httpx2.MockTransport(handle), trust_env=False)
        client = OpenAIModelClient(
            "fictional-model", "FAKE_KEY_FOR_TEST_ONLY", http_client=transport
        )
        try:
            result = await client.complete(request())
            assert result.tool_calls[0].id == "call_fictional"
            assert (result.tokens_in, result.tokens_out) == (10, 3)
            assert len(client._agent.tools) == 3 and not client._agent.handoffs
            assert not client._agent.mcp_servers
        finally:
            await client.aclose()
        assert transport.is_closed

    asyncio.run(scenario())
    assert len(captured) == 1
    assert str(captured[0].url) == "https://api.openai.com/v1/responses"
    payload = json.loads(captured[0].content)
    assert payload["model"] == "fictional-model" and payload["max_output_tokens"] == 2000
    assert payload["parallel_tool_calls"] is False and payload["store"] is False
    assert payload["truncation"] == "disabled"
    assert {item["type"] for item in payload["tools"]} == {"function"}
    assert {item["name"] for item in payload["tools"]} == {t.name for t in tool_definitions()}
    assert "previous_response_id" not in payload and "conversation" not in payload


def test_provider_reasoning_and_tool_ids_survive_stateless_continuation():
    captured = []

    def handle(incoming):
        captured.append(json.loads(incoming.content))
        if len(captured) == 1:
            output = [
                {
                    "type": "reasoning",
                    "id": "rs_fictional",
                    "summary": [],
                    "encrypted_content": "FICTIONAL_OPAQUE_STATE",
                },
                {
                    "type": "function_call",
                    "id": "fc_fictional",
                    "call_id": "call_fictional",
                    "name": "get_project_overview",
                    "arguments": '{"project_id":"demo"}',
                    "status": "completed",
                },
            ]
        else:
            output = [
                {
                    "type": "message",
                    "id": "msg_fictional",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Fictional cited response.",
                            "annotations": [],
                        }
                    ],
                }
            ]
        return httpx2.Response(200, json=response_body(output))

    async def scenario():
        client = OpenAIModelClient(
            "fictional-model",
            "FAKE_KEY",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        )
        try:
            first = await client.complete(request())
            messages = [
                *request().messages,
                Message("assistant", tool_calls=first.tool_calls, continuation=first.continuation),
                Message("tool", '{"sources":[]}', call_id="call_fictional"),
            ]
            second = await client.complete(request(messages, tools=()))
            assert second.text == "Fictional cited response."
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert any(
        item.get("encrypted_content") == "FICTIONAL_OPAQUE_STATE" for item in captured[1]["input"]
    )
    assert captured[1]["input"][-1]["call_id"] == "call_fictional"
    assert captured[1]["tool_choice"] == "none"
    assert not captured[1].get("tools")


@pytest.mark.parametrize("status", [401, 429, 500, 302])
def test_sdk_errors_are_redacted_without_retries_or_redirects(status):
    calls = []

    def handle(incoming):
        calls.append(incoming)
        return httpx2.Response(
            status,
            json={"error": {"message": "BODY-CANARY FAKE_KEY"}},
            headers={"Location": "https://must-not-follow.invalid"},
        )

    async def scenario():
        client = OpenAIModelClient(
            "fictional-model",
            "FAKE_KEY",
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(handle), follow_redirects=False
            ),
        )
        try:
            with pytest.raises(WorkPilotError) as caught:
                await client.complete(request())
            assert (
                caught.value.code == 5
                and "CANARY" not in str(caught.value)
                and "FAKE_KEY" not in str(caught.value)
            )
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert len(calls) == 1


def test_sdk_runtime_tracing_is_off_and_doctor_needs_no_key(agent_context, monkeypatch):
    def no_client(*_args, **_kwargs):
        raise AssertionError("doctor must not construct an OpenAI client.")

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", no_client)
    sdk_version, disabled = tracing_status()
    assert sdk_version == SDK_VERSION and disabled
    assert os.environ["OPENAI_AGENTS_DISABLE_TRACING"] == "1"
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "SDK 运行时 tracing 已关闭（NoOpTrace）" in result.output
    assert "OPENAI_API_KEY: 未设置" in result.output


def test_doctor_reports_bad_tracing_environment_without_repair(agent_context, monkeypatch):
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "0")
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 6
    assert os.environ["OPENAI_AGENTS_DISABLE_TRACING"] == "0"


def test_sdk_imports_are_confined_to_one_provider_implementation():
    import ast
    from pathlib import Path

    root = Path(__file__).parents[1] / "src/workpilot"
    importers = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(name == "agents" or name.startswith("agents.") for name in modules):
                importers.add(path.relative_to(root).as_posix())
    assert importers == {"llm/openai_client.py"}
