"""T04 CLI permission/configuration gates, metadata-only previews and offline use."""

import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from workpilot.agent import runtime
from workpilot.cli import app
from workpilot.config import load_settings
from workpilot.llm.client import ModelResponse, StubModel, ToolCall, json_text
from workpilot.security.policy import SourcePolicy


def forbidden(*_args, **_kwargs):
    raise AssertionError("This path must not create a model client or read source bytes.")


def configure(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "FAKE_KEY_FOR_OFFLINE_TEST_ONLY")
    monkeypatch.setenv("WORKPILOT_MODEL", "fictional-model")


def scripted(context):
    return StubModel(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "call-1",
                        "search_project_sources",
                        json_text({"project_id": context.project.id, "query": "风险"}),
                    ),
                )
            ),
            ModelResponse("测试书目数量不足。[STATUS.md:L2-L3]"),
        ]
    )


@pytest.mark.parametrize("arguments", [[], ["--allow-remote"]])
def test_dry_run_needs_no_key_or_permission_and_prints_no_material_text(
    agent_context, monkeypatch, arguments
):
    monkeypatch.setattr(runtime, "create_model_client", forbidden)
    monkeypatch.setattr(SourcePolicy, "read_source", forbidden)
    result = CliRunner().invoke(app, ["ask", "demo", "当前风险是什么？", "--dry-run", *arguments])
    assert result.exit_code == 0, result.output
    assert "STATUS.md | L2-L3" in result.output and "估算 tokens" in result.output
    assert "测试书目数量不足" not in result.output and "人工决定" not in result.output
    assert "当前风险是什么？" not in result.output


def test_dry_run_takes_priority_over_remote_environment(agent_context, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("WORKPILOT_SEND_TO_MODEL", "true")
    monkeypatch.setattr(runtime, "create_model_client", forbidden)
    result = CliRunner().invoke(app, ["ask", "demo", "风险", "--dry-run", "--allow-remote"])
    assert result.exit_code == 0 and "未调用模型" in result.output


def test_no_permission_returns_four_before_constructing_a_client(agent_context, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(runtime, "create_model_client", forbidden)
    result = CliRunner().invoke(app, ["ask", "demo", "QUESTION-CANARY 风险"])
    assert result.exit_code == 4 and "--allow-remote" in result.output
    assert "QUESTION-CANARY" not in result.output


@pytest.mark.parametrize("missing", ["OPENAI_API_KEY", "WORKPILOT_MODEL"])
def test_missing_configuration_returns_six_without_external_access(
    agent_context, monkeypatch, missing
):
    configure(monkeypatch)
    monkeypatch.delenv(missing)
    monkeypatch.setattr(runtime, "create_model_client", forbidden)
    result = CliRunner().invoke(app, ["ask", "demo", "风险", "--allow-remote"])
    assert result.exit_code == 6
    assert "FAKE_KEY_FOR_OFFLINE_TEST_ONLY" not in result.output


def test_one_off_permission_does_not_persist(agent_context, monkeypatch):
    configure(monkeypatch)
    model = scripted(agent_context)
    monkeypatch.setattr(runtime, "create_model_client", lambda _settings: model)
    runner = CliRunner()
    accepted = runner.invoke(app, ["ask", "demo", "风险", "--allow-remote"])
    denied = runner.invoke(app, ["ask", "demo", "风险"])
    assert accepted.exit_code == 0 and "[STATUS.md:L2-L3] indexed_at=" in accepted.output
    assert denied.exit_code == 4 and len(model.requests) == 2
    assert os.environ["WORKPILOT_SEND_TO_MODEL"] == "false"


def test_explicit_remote_environment_is_honored(agent_context, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("WORKPILOT_SEND_TO_MODEL", "true")
    monkeypatch.setattr(runtime, "create_model_client", lambda _settings: scripted(agent_context))
    result = CliRunner().invoke(app, ["ask", "demo", "风险"])
    assert result.exit_code == 0 and "测试书目数量不足" in result.output


def test_zero_hits_fails_closed_without_even_constructing_a_client(agent_context, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(runtime, "create_model_client", forbidden)
    result = CliRunner().invoke(app, ["ask", "demo", "没有命中的关键词", "--allow-remote"])
    assert result.exit_code == 0 and "现有索引不足" in result.output


@pytest.mark.parametrize(
    "arguments, code",
    [
        (["ask", "unknown", "风险", "--dry-run"], 3),
        (["ask", "unknown", "风险", "--allow-remote"], 3),
        (["ask", "demo"], 2),
        (["ask", "demo", "  ", "--dry-run"], 2),
        (["ask", "demo", "  ", "--allow-remote"], 2),
    ],
)
def test_missing_project_and_invalid_usage_have_defined_exit_codes(agent_context, arguments, code):
    assert CliRunner().invoke(app, arguments).exit_code == code


def test_fresh_process_dry_run_does_not_import_sdk_or_load_a_model(agent_context):
    script = """import sys
from typer.testing import CliRunner
from workpilot.cli import app
result = CliRunner().invoke(app, ['ask', 'demo', '风险', '--dry-run'])
assert result.exit_code == 0, result.output
assert 'agents' not in sys.modules
assert 'openai' not in sys.modules
print('PASS: dry-run without SDK imports')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_remote_errors_do_not_echo_response_or_key(agent_context, monkeypatch):
    configure(monkeypatch)
    model = StubModel([RuntimeError("CANARY response includes FAKE_KEY_FOR_OFFLINE_TEST_ONLY")])
    monkeypatch.setattr(runtime, "create_model_client", lambda _settings: model)
    result = CliRunner().invoke(app, ["ask", "demo", "风险", "--allow-remote"])
    assert result.exit_code == 5 and "CANARY" not in result.output
    assert "FAKE_KEY_FOR_OFFLINE_TEST_ONLY" not in result.output


def test_dry_run_never_modifies_remote_permission_setting(agent_context):
    before = load_settings()
    assert (
        CliRunner().invoke(app, ["ask", "demo", "风险", "--dry-run", "--allow-remote"]).exit_code
        == 0
    )
    assert load_settings() == before


def test_model_client_initialization_errors_are_redacted(agent_context, monkeypatch):
    configure(monkeypatch)

    def broken(_settings):
        raise ValueError("INITIALIZE-CANARY FAKE_KEY_FOR_OFFLINE_TEST_ONLY")

    monkeypatch.setattr(runtime, "create_model_client", broken)
    result = CliRunner().invoke(app, ["ask", "demo", "风险", "--allow-remote"])
    assert result.exit_code == 6 and "CANARY" not in result.output
    assert "FAKE_KEY_FOR_OFFLINE_TEST_ONLY" not in result.output
