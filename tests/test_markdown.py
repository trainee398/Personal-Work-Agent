"""T02 / §§6.2–6.3, 10: normalization, hashes and reproducible original lines."""

from hashlib import sha256

import pytest

from workpilot.errors import PolicyError
from workpilot.ingestion.markdown import chunk_markdown, normalize_markdown, original_lines


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
@pytest.mark.parametrize("bom", [False, True], ids=["plain", "bom"])
def test_normalization_preserves_chinese_line_numbers_and_chunk_hashes(ending, bom):
    text = "# 虚构项目\n中文正文\n## 下一步\n只读验证\n"
    raw = (b"\xef\xbb\xbf" if bom else b"") + text.replace("\n", ending).encode()
    normalized = normalize_markdown(raw)
    assert normalized == text
    chunks = chunk_markdown(normalized)
    assert [(chunk.line_start, chunk.line_end) for chunk in chunks] == [(1, 2), (3, 4)]
    assert [chunk.heading for chunk in chunks] == ["虚构项目", "虚构项目 > 下一步"]
    assert "".join(chunk.body for chunk in chunks) == text
    assert all(chunk.content_hash == sha256(chunk.body.encode()).hexdigest() for chunk in chunks)
    assert chunks == chunk_markdown(normalize_markdown(text.encode()))


@pytest.mark.parametrize("raw", [b"\xff\xfe", b"valid\x00binary"])
def test_invalid_encoding_or_binary_text_is_explicitly_denied(raw):
    with pytest.raises(PolicyError):
        normalize_markdown(raw)


def test_only_lf_counts_as_a_physical_line_break():
    text = "# 标题\n正文\u2028仍在第二行\x0b\u0085\u2029\n末行"
    assert original_lines(text) == ["# 标题\n", "正文\u2028仍在第二行\x0b\u0085\u2029\n", "末行"]
    chunk = chunk_markdown(text)[0]
    assert (chunk.line_start, chunk.line_end, chunk.body) == (1, 3, text)


def test_atx_levels_breadcrumbs_and_inclusive_ranges():
    text = (
        "# 项目\n正文\n## 进展 ##\n进展正文\n### 风险\n阻塞正文\n"
        "#### 四级标题属于正文\n风险补充\n## 下一步\n# 另一个虚构项目\n"
    )
    chunks = chunk_markdown(text)
    assert [(c.heading, c.line_start, c.line_end) for c in chunks] == [
        ("项目", 1, 2),
        ("项目 > 进展", 3, 4),
        ("项目 > 进展 > 风险", 5, 8),
        ("项目 > 下一步", 9, 9),
        ("另一个虚构项目", 10, 10),
    ]
    assert "".join(c.body for c in chunks) == text


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_headings_in_fences_do_not_change_breadcrumbs(fence):
    text = f"# 真标题\n{fence}markdown\n## 代码中的标题\n{fence}\n## 下一节\n正文\n"
    chunks = chunk_markdown(text)
    assert [(c.heading, c.line_start, c.line_end) for c in chunks] == [
        ("真标题", 1, 4),
        ("真标题 > 下一节", 5, 6),
    ]


def test_non_atx_and_indented_headings_remain_body():
    text = "无标题\n======\n#缺少空格\n    ## 缩进代码\n#### 四级\n"
    chunk = chunk_markdown(text)[0]
    assert chunk.heading is None
    assert (chunk.line_start, chunk.line_end, chunk.body) == (1, 5, text)


def test_headingless_paragraphs_are_split_and_small_neighbors_merge():
    text = "甲" * 220 + "\n\n" + "乙" * 220 + "\n\n" + "短段\n"
    chunks = chunk_markdown(text)
    assert [(c.line_start, c.line_end) for c in chunks] == [(1, 2), (3, 5)]
    assert all(c.heading is None for c in chunks)
    assert "".join(c.body for c in chunks) == text


def test_small_sections_keep_their_own_headings():
    chunks = chunk_markdown("# 甲\n小节甲\n# 乙\n小节乙\n")
    assert [c.heading for c in chunks] == ["甲", "乙"]


def test_long_section_is_deterministic_bounded_and_has_no_fabricated_lines():
    text = "# 虚构长文\n\n" + ("中文段落" * 230 + "\n\n") * 4
    chunks = chunk_markdown(text)
    lines = original_lines(text)
    assert len(chunks) == 4
    assert chunks == chunk_markdown(text)
    assert chunks[0].line_start == 1
    assert chunks[-1].line_end == len(lines)
    assert all(c.heading == "虚构长文" and len(c.body) <= 2000 for c in chunks)
    assert all(c.body == "".join(lines[c.line_start - 1 : c.line_end]) for c in chunks)
    assert all(
        left.line_end + 1 == right.line_start
        for left, right in zip(chunks, chunks[1:], strict=False)
    )
    assert "".join(c.body for c in chunks) == text
    assert "# 虚构长文" not in chunks[1].body


def test_long_paragraph_splits_only_at_complete_original_lines():
    text = "# 长段\n" + ("文" * 99 + "\n") * 50
    chunks = chunk_markdown(text)
    assert len(chunks) == 3
    assert all(len(c.body) <= 2000 for c in chunks)
    assert "".join(c.body for c in chunks) == text
    assert all(c.body.endswith("\n") for c in chunks)
    assert chunks[-1].line_end == 51


def test_character_limit_counts_unicode_characters_and_newlines():
    assert len(chunk_markdown("中" * 1999 + "\n")[0].body) == 2000
    assert len(chunk_markdown("中" * 2000)[0].body) == 2000
    with pytest.raises(PolicyError, match="单行"):
        chunk_markdown("中" * 2000 + "\n")


@pytest.mark.parametrize("raw", [b"", b"\xef\xbb\xbf"])
def test_empty_document_has_no_chunks(raw):
    assert chunk_markdown(normalize_markdown(raw)) == []
