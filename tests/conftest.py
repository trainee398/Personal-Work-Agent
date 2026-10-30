"""Keep tests independent of user configuration and remote services."""

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
    monkeypatch.setenv("WORKPILOT_DATA_DIR", str(data_dir))
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    return data_dir
