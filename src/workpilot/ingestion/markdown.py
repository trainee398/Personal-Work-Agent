"""Normalize newlines and split contiguous original lines without inventing text."""

import re
from hashlib import sha256

from workpilot.config import CHUNK_MAX_CHARS, CHUNK_MERGE_MIN_CHARS
from workpilot.domain.models import SourceChunk
from workpilot.errors import PolicyError

ATX_HEADING = re.compile(r"^ {0,3}(#{1,3})(?:[ \t]+(.*)|[ \t]*)$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def normalize_markdown(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise PolicyError("源文件必须是 UTF-8 文本。") from None
    if "\x00" in text:
        raise PolicyError("源文件包含二进制空字符，不能作为 Markdown 索引。")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def original_lines(text: str) -> list[str]:
    """Split only physical LF line endings, keeping terminators and line numbers."""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _sections(lines: list[str]) -> list[tuple[int, int, str | None]]:
    sections: list[tuple[int, int, str | None]] = []
    headings: dict[int, str] = {}
    start = 0
    heading = None
    fence_character = ""
    fence_length = 0
    for index, line in enumerate(lines):
        content = line.removesuffix("\n")
        marker = FENCE.match(content)
        if fence_character:
            if (
                marker
                and marker[1][0] == fence_character
                and len(marker[1]) >= fence_length
                and not marker[2].strip()
            ):
                fence_character = ""
            continue
        if marker and not (marker[1][0] == "`" and "`" in marker[2]):
            fence_character = marker[1][0]
            fence_length = len(marker[1])
            continue
        match = ATX_HEADING.match(content)
        if match is None:
            continue
        if index > start:
            sections.append((start, index, heading))
        level = len(match[1])
        title = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", match[2] or "").strip()
        headings = {depth: value for depth, value in headings.items() if depth < level}
        headings[level] = title
        heading = " > ".join(value for _, value in sorted(headings.items()) if value) or None
        start = index
    if start < len(lines):
        sections.append((start, len(lines), heading))
    return sections


def _paragraphs(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    spans = []
    beginning = start
    for index in range(start, end):
        if not lines[index].strip() and (index + 1 == end or lines[index + 1].strip()):
            spans.append((beginning, index + 1))
            beginning = index + 1
    if beginning < end:
        spans.append((beginning, end))
    return spans


def chunk_markdown(
    text: str,
    *,
    max_chars: int = CHUNK_MAX_CHARS,
    merge_min_chars: int = CHUNK_MERGE_MIN_CHARS,
) -> list[SourceChunk]:
    """Each body is exactly its normalized source-line interval, with no overlap."""
    if max_chars < 1 or not 0 <= merge_min_chars <= max_chars:
        raise ValueError("Invalid chunk size settings.")
    lines = original_lines(text)
    if any(len(line) > max_chars for line in lines):
        raise PolicyError("单行超过切片字符上限，无法保留完整行号；请先拆分该行。")
    lengths = [0]
    for line in lines:
        lengths.append(lengths[-1] + len(line))
    chunks = []
    for start, end, heading in _sections(lines):
        if heading is not None and lengths[end] - lengths[start] <= max_chars:
            spans = [(start, end)]
        else:
            spans = []
            for beginning, ending in _paragraphs(lines, start, end):
                piece_start, size = beginning, 0
                for index in range(beginning, ending):
                    if size + len(lines[index]) > max_chars:
                        spans.append((piece_start, index))
                        piece_start, size = index, 0
                    size += len(lines[index])
                if piece_start < ending:
                    spans.append((piece_start, ending))
            merged: list[tuple[int, int]] = []
            for beginning, ending in spans:
                size = lengths[ending] - lengths[beginning]
                if merged:
                    previous_start, previous_end = merged[-1]
                    previous_size = lengths[previous_end] - lengths[previous_start]
                    if (
                        previous_end == beginning
                        and min(size, previous_size) < merge_min_chars
                        and size + previous_size <= max_chars
                    ):
                        merged[-1] = (previous_start, ending)
                        continue
                merged.append((beginning, ending))
            spans = merged
        for beginning, ending in spans:
            body = "".join(lines[beginning:ending])
            chunks.append(
                SourceChunk(
                    heading=heading,
                    line_start=beginning + 1,
                    line_end=ending,
                    body=body,
                    content_hash=sha256(body.encode("utf-8")).hexdigest(),
                )
            )
    return chunks
