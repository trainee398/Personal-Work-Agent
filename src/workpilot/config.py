"""Explicit environment configuration and the shared Appendix B defaults."""

import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from workpilot.errors import WorkPilotError

MAX_FILE_BYTES = 1024 * 1024
ALLOWED_EXTENSIONS = (".md", ".markdown")
CHUNK_MAX_CHARS = 2000
CHUNK_MERGE_MIN_CHARS = 200
TOP_K_CHUNKS = 8
MAX_CONTEXT_CHARS = 12000
EXCERPT_MAX_CHARS = 400
MAX_TOOL_ROUNDS = 6
MODEL_TIMEOUT_S = 60
ASK_TOTAL_TIMEOUT_S = 120
MAX_OUTPUT_TOKENS = 2000
STALE_DAYS = 7
SUMMARY_MAX_CHARS = 200
LOCK_TIMEOUT_S = 0
DATA_DIR_MODE = 0o700
DATABASE_MODE = 0o600
DATABASE_NAME = "workpilot.db"
LOCK_NAME = "workpilot.lock"

DEFAULT_PARAMETERS = MappingProxyType(
    {
        "MAX_FILE_BYTES": MAX_FILE_BYTES,
        "ALLOWED_EXTENSIONS": ALLOWED_EXTENSIONS,
        "CHUNK_MAX_CHARS": CHUNK_MAX_CHARS,
        "CHUNK_MERGE_MIN_CHARS": CHUNK_MERGE_MIN_CHARS,
        "TOP_K_CHUNKS": TOP_K_CHUNKS,
        "MAX_CONTEXT_CHARS": MAX_CONTEXT_CHARS,
        "EXCERPT_MAX_CHARS": EXCERPT_MAX_CHARS,
        "MAX_TOOL_ROUNDS": MAX_TOOL_ROUNDS,
        "MODEL_TIMEOUT_S": MODEL_TIMEOUT_S,
        "ASK_TOTAL_TIMEOUT_S": ASK_TOTAL_TIMEOUT_S,
        "MAX_OUTPUT_TOKENS": MAX_OUTPUT_TOKENS,
        "SUMMARY_MAX_CHARS": SUMMARY_MAX_CHARS,
        "LOCK_TIMEOUT_S": LOCK_TIMEOUT_S,
        "QUESTION_STORAGE": "SHA-256 + 字符数",
        "DATA_DIR_MODE": "0700",
        "DATABASE_MODE": "0600",
    }
)


def has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    data_dir: Path
    model: str | None = Field(default=None, max_length=200)
    api_key_set: bool = False
    send_to_model: bool = False
    stale_days: int = Field(default=STALE_DAYS, ge=1)

    @property
    def database_path(self) -> Path:
        return self.data_dir / DATABASE_NAME

    @property
    def lock_path(self) -> Path:
        return self.data_dir / LOCK_NAME


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Read only named settings; never load .env or retain an API key."""
    env = os.environ if environ is None else environ
    raw_dir = env.get("WORKPILOT_DATA_DIR", "").strip()
    raw_model = env.get("WORKPILOT_MODEL", "").strip()
    if has_control_characters(raw_dir) or has_control_characters(raw_model):
        raise WorkPilotError("配置路径或模型名包含控制字符。")

    raw_send = env.get("WORKPILOT_SEND_TO_MODEL", "false").strip().lower()
    if raw_send not in {"true", "false"}:
        raise WorkPilotError("WORKPILOT_SEND_TO_MODEL 必须为 true 或 false。")
    raw_stale = env.get("WORKPILOT_STALE_DAYS", str(STALE_DAYS)).strip()
    try:
        stale_days = int(raw_stale)
        if stale_days < 1:
            raise ValueError
    except ValueError:
        raise WorkPilotError("WORKPILOT_STALE_DAYS 必须为正整数。") from None

    try:
        data_dir = Path(raw_dir).expanduser() if raw_dir else Path.home() / ".local/share/workpilot"
        if ".." in data_dir.parts:
            raise WorkPilotError("WORKPILOT_DATA_DIR 不允许包含父目录跳转。")
        data_dir = data_dir.absolute()
        if data_dir in {Path(data_dir.anchor), Path.home()}:
            raise WorkPilotError("请为 WorkPilot 指定独立的数据子目录。")
        if {".git", ".ssh", ".aws", ".gnupg", ".codex", ".agents", ".claude"} & set(data_dir.parts):
            raise WorkPilotError("运行数据目录不得位于敏感配置目录内。")
        return Settings(
            data_dir=data_dir,
            model=raw_model or None,
            api_key_set=bool(env.get("OPENAI_API_KEY", "").strip()),
            send_to_model=raw_send == "true",
            stale_days=stale_days,
        )
    except (OSError, RuntimeError, ValidationError):
        raise WorkPilotError("WorkPilot 配置无效，请检查路径和模型名设置。") from None
