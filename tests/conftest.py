"""Keep tests independent of user configuration and remote services."""

import shutil
import socket
from pathlib import Path
from typing import NoReturn

import pytest


def deny_network(*_args: object, **_kwargs: object) -> NoReturn:
    raise AssertionError("WorkPilot tests must not use the network.")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Use disposable data, no model credentials, and blocked DNS/TCP calls."""
    data_dir = tmp_path / "workpilot-data"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WORKPILOT_MODEL", raising=False)
    monkeypatch.setenv("WORKPILOT_SEND_TO_MODEL", "false")
    monkeypatch.setenv("WORKPILOT_STALE_DAYS", "7")
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "1")
    monkeypatch.setenv("WORKPILOT_DATA_DIR", str(data_dir))
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    return data_dir


@pytest.fixture
def demo_project(tmp_path: Path) -> Path:
    source = Path(__file__).parent / "fixtures" / "demo_project"
    destination = tmp_path / "fictional-project"
    shutil.copytree(source, destination)
    return destination


@pytest.fixture
def registered_project(demo_project: Path):
    """T02 starts from an ordinary T01 registration of fictional data."""
    from workpilot.config import load_settings
    from workpilot.projects import ProjectRegistry

    return ProjectRegistry(load_settings()).add("demo", demo_project, ["STATUS.md"])


@pytest.fixture
def agent_context(registered_project, demo_project: Path):
    """A small undated fictional source and an explicitly separate manual decision."""
    from workpilot.agent.context import prepare_context
    from workpilot.config import load_settings
    from workpilot.domain.models import MemoryKind
    from workpilot.ingestion.sync import sync_project
    from workpilot.memory import MemoryStore

    (demo_project / "STATUS.md").write_text(
        "# 虚构项目\n## 风险\n测试书目数量不足，可能影响虚构检索演示。\n"
        "## 下一步\n先补充纯虚构书目。\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert sync_project("demo", settings).exit_code == 0
    MemoryStore(settings).add("demo", MemoryKind.DECISION, "人工决定：先安排一次虚构评审。")
    return prepare_context("demo", "当前风险是什么？", settings)
