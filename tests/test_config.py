"""Configuration failures must not reveal the supplied secret-like values."""

from pathlib import Path

import pytest

from workpilot.config import load_settings
from workpilot.errors import ExitCode, WorkPilotError


def test_environment_defaults_without_a_model_key() -> None:
    settings = load_settings({})

    assert settings.data_dir == Path.home() / ".local/share/workpilot"
    assert settings.stale_days == 7
    assert settings.send_to_model is False
    assert settings.api_key_set is False
    assert settings.model is None


def test_explicit_configuration_does_not_retain_api_key(tmp_path: Path) -> None:
    fake_key = "FAKE_KEY_VALUE_DO_NOT_PRINT"
    settings = load_settings(
        {
            "WORKPILOT_DATA_DIR": str(tmp_path / "data"),
            "WORKPILOT_MODEL": "fictional-model",
            "WORKPILOT_SEND_TO_MODEL": "true",
            "WORKPILOT_STALE_DAYS": "14",
            "OPENAI_API_KEY": fake_key,
        }
    )

    assert settings.api_key_set is True
    assert settings.model == "fictional-model"
    assert settings.send_to_model is True
    assert settings.stale_days == 14
    assert fake_key not in repr(settings)
    assert fake_key not in settings.model_dump_json()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WORKPILOT_SEND_TO_MODEL", "yes"),
        ("WORKPILOT_SEND_TO_MODEL", "FAKE_PRIVATE_INPUT"),
        ("WORKPILOT_STALE_DAYS", "FAKE_PRIVATE_INPUT"),
        ("WORKPILOT_STALE_DAYS", "0"),
        ("WORKPILOT_STALE_DAYS", "-1"),
        ("WORKPILOT_MODEL", "bad\x1bmodel"),
        ("WORKPILOT_DATA_DIR", "/"),
        ("WORKPILOT_DATA_DIR", "../outside"),
        ("WORKPILOT_DATA_DIR", "~/.ssh/workpilot"),
    ],
)
def test_invalid_settings_fail_safely(name: str, value: str) -> None:
    with pytest.raises(WorkPilotError) as caught:
        load_settings({name: value})

    assert caught.value.code == ExitCode.CONFIGURATION
    assert "FAKE_PRIVATE_INPUT" not in str(caught.value)


def test_blank_example_data_dir_and_model_use_defaults() -> None:
    settings = load_settings({"WORKPILOT_DATA_DIR": "", "WORKPILOT_MODEL": ""})

    assert settings.data_dir == Path.home() / ".local/share/workpilot"
    assert settings.model is None
