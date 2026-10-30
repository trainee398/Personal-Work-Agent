"""Validated project records and immutable source snapshots."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from workpilot.config import has_control_characters


class ProjectRegistration(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=100)
    root: Path
    allowed_files: tuple[str, ...] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if value != value.strip() or has_control_characters(value):
            raise ValueError("项目名不能包含控制字符或首尾空白。")
        return value


class Project(ProjectRegistration):
    id: str
    active: bool = True
    created_at: str
    updated_at: str


class SourceChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    heading: str | None
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    body: str = Field(repr=False)
    content_hash: str


class SourceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    relative_path: str
    text: str = Field(repr=False)
    sha256: str
    size_bytes: int
    file_mtime_utc: str
