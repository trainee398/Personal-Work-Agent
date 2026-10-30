"""T00 acceptance: help and usage errors work without configuration."""

import importlib
import os
import subprocess
import sys
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


@pytest.mark.parametrize(
    "arguments",
    [
        ["init", "--help"],
        ["doctor", "--help"],
        ["project", "--help"],
        ["project", "add", "--help"],
        ["project", "list", "--help"],
        ["project", "remove", "--help"],
        ["status", "--help"],
        ["memory", "--help"],
        ["memory", "add", "--help"],
        ["memory", "list", "--help"],
        ["ask", "--help"],
    ],
)
def test_command_help_does_not_load_invalid_configuration(
    arguments: list[str], isolated_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workpilot.cli import app

    monkeypatch.setenv("WORKPILOT_STALE_DAYS", "invalid")
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 0
    assert not isolated_environment.exists()


def test_project_cli_persists_across_processes(demo_project: Path) -> None:
    executable = str(Path(sys.executable).with_name("workpilot"))
    add = subprocess.run(
        [executable, "project", "add", "demo", "--root", str(demo_project), "--file", "STATUS.md"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert add.returncode == 0, add.stdout + add.stderr
    listed = subprocess.run(
        [executable, "project", "list"], capture_output=True, text=True, check=False
    )
    assert listed.returncode == 0, listed.stderr
    assert "demo" in listed.stdout and "STATUS.md" in listed.stdout


def test_purge_needs_confirmation_and_can_be_cancelled(demo_project: Path) -> None:
    from workpilot.cli import app
    from workpilot.config import load_settings
    from workpilot.projects import ProjectRegistry

    registry = ProjectRegistry(load_settings())
    registry.add("demo", demo_project, ["STATUS.md"])
    runner = CliRunner()
    cancelled = runner.invoke(app, ["project", "remove", "demo", "--purge"], input="n\n")
    assert cancelled.exit_code == 0
    assert registry.get("demo").active
    no_input = runner.invoke(app, ["project", "remove", "demo", "--purge"], input="")
    assert no_input.exit_code == 2
    assert registry.get("demo").active
    confirmed = runner.invoke(app, ["project", "remove", "demo", "--purge"], input="y\n")
    assert confirmed.exit_code == 0, confirmed.output
    assert registry.list() == []


def test_cli_usage_missing_record_and_policy_exit_codes(demo_project: Path) -> None:
    from workpilot.cli import app

    runner = CliRunner()
    assert runner.invoke(app, ["project", "add", "demo"]).exit_code == 2
    assert runner.invoke(app, ["project", "remove", "missing"]).exit_code == 3
    denied = runner.invoke(
        app, ["project", "add", "demo", "--root", str(demo_project), "--file", ".env"]
    )
    assert denied.exit_code == 4


def test_init_is_idempotent_through_the_cli(demo_project: Path) -> None:
    from workpilot.cli import app
    from workpilot.config import load_settings
    from workpilot.projects import ProjectRegistry

    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0
    original = ProjectRegistry(load_settings()).add("demo", demo_project, ["STATUS.md"])
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert ProjectRegistry(load_settings()).get("demo") == original
