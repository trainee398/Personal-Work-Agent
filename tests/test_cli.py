"""T00 acceptance: help and usage errors work without configuration."""

import importlib
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.mark.parametrize("arguments", [[], ["--help"]], ids=["no-arguments", "help"])
def test_help_is_offline_and_does_not_create_data(
    arguments: list[str], isolated_environment: Path
) -> None:
    from workpilot.cli import app

    result = CliRunner().invoke(app, arguments, color=False)

    assert result.exit_code == 0, result.output
    assert "WorkPilot" in result.output
    assert "--help" in result.output
    assert not isolated_environment.exists()


def test_unknown_command_returns_usage_error(isolated_environment: Path) -> None:
    from workpilot.cli import app

    result = CliRunner().invoke(app, ["unknown-command"], color=False)

    assert result.exit_code == 2
    assert not isolated_environment.exists()


@pytest.mark.parametrize("inherited_value", [None, "0"], ids=["unset", "enabled-by-parent"])
def test_package_disables_tracing(
    monkeypatch: pytest.MonkeyPatch, inherited_value: str | None
) -> None:
    import workpilot

    if inherited_value is None:
        monkeypatch.delenv("OPENAI_AGENTS_DISABLE_TRACING", raising=False)
    else:
        monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", inherited_value)

    importlib.reload(workpilot)

    assert os.environ["OPENAI_AGENTS_DISABLE_TRACING"] == "1"
