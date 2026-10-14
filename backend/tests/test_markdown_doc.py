from sagent.services.markdown_doc import (
    normalize_heading,
    parse_markdown,
    render_markdown,
    strip_heading_number,
)

DOC = """# 登入功能

> 文件類型：功能規格書

## 1. 目的與範圍

讓使用者登入。

## 2. 業務流程

```mermaid
flowchart TD
## 這不是標題
A --> B
```

說明文字。
"""


def test_parse_sections_ignores_headings_in_code_blocks():
    doc = parse_markdown(DOC)
    assert doc.title == "登入功能"
    assert [s.heading for s in doc.sections] == ["1. 目的與範圍", "2. 業務流程"]
    assert "## 這不是標題" in doc.sections[1].body
    assert doc.sections[1].body.endswith("說明文字。")


def test_render_round_trip():
    doc = parse_markdown(DOC)
    again = parse_markdown(render_markdown(doc))
    assert [(s.heading, s.body) for s in again.sections] == [(s.heading, s.body) for s in doc.sections]
    assert again.preamble == doc.preamble


def test_doc_without_sections():
    doc = parse_markdown("# 標題\n\n只有內文")
    assert doc.sections == []
    assert "只有內文" in doc.preamble


def test_strip_heading_number():
    assert strip_heading_number("3. 業務流程") == "業務流程"
    assert strip_heading_number("2.1 範圍") == "範圍"
    assert strip_heading_number("一、目的") == "目的"
    assert strip_heading_number("第三章 系統設計") == "系統設計"
    assert strip_heading_number("2026 年度規劃") == "2026 年度規劃"
    assert strip_heading_number("3.5G 網路") == "3.5G 網路"
    assert normalize_heading("1. 目的 與 範圍") == normalize_heading("目的與範圍")
