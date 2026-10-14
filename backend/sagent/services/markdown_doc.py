"""把 Markdown 文件拆成章節（以 `## ` 二級標題分節），也能組回去。

程式碼區塊（``` 或 ~~~）裡的 `##` 不算標題。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_H2_RE = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_H1_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$")
# 標題前面的編號，例如「1.」「2.3」「一、」「第三章」「(1)」
_NUM_PREFIX_RE = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百0-9]+[章節部分]\s*|[0-9]{1,2}(?:\.[0-9]+)*(?:[.、)](?![0-9])\s*|\s+)|"
    r"[一二三四五六七八九十]+[、.]\s*|\(?[0-9]+\)\s*)"
)


@dataclass
class MdSection:
    heading: str
    body: str = ""


@dataclass
class MdDoc:
    preamble: str = ""
    sections: list[MdSection] = field(default_factory=list)

    @property
    def title(self) -> str:
        for line in self.preamble.splitlines():
            m = _H1_RE.match(line)
            if m:
                return m.group(1).strip()
        return ""


def iter_lines_with_fence_state(text: str):
    """逐行回傳 (行內容, 是否在程式碼區塊內)。區塊的開頭和結尾那行也算在區塊內。"""
    fence: str | None = None
    for line in text.splitlines():
        m = _FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                yield line, True
                continue
            yield line, False
        else:
            if (
                m
                and m.group(1)[0] == fence[0]
                and len(m.group(1)) >= len(fence)
                and line.strip() == m.group(1)
            ):
                fence = None
            yield line, True


def parse_markdown(text: str) -> MdDoc:
    doc = MdDoc()
    current: MdSection | None = None
    pre: list[str] = []
    body: list[str] = []
    for line, in_fence in iter_lines_with_fence_state(text):
        m = None if in_fence else _H2_RE.match(line)
        if m:
            if current is None:
                doc.preamble = "\n".join(pre).strip("\n")
            else:
                current.body = "\n".join(body).strip("\n")
                doc.sections.append(current)
            current = MdSection(heading=m.group(1).strip())
            body = []
        elif current is None:
            pre.append(line)
        else:
            body.append(line)
    if current is None:
        doc.preamble = "\n".join(pre).strip("\n")
    else:
        current.body = "\n".join(body).strip("\n")
        doc.sections.append(current)
    return doc


def render_markdown(doc: MdDoc) -> str:
    parts: list[str] = []
    if doc.preamble.strip():
        parts.append(doc.preamble.strip("\n"))
    for sec in doc.sections:
        block = f"## {sec.heading}"
        if sec.body.strip():
            block += "\n\n" + sec.body.strip("\n")
        parts.append(block)
    return "\n\n".join(parts).rstrip() + "\n"


def strip_heading_number(heading: str) -> str:
    """去掉標題前面的編號：「3. 業務流程」→「業務流程」。"""
    prev = None
    text = heading.strip()
    while prev != text:
        prev = text
        text = _NUM_PREFIX_RE.sub("", text, count=1).strip()
    return text or heading.strip()


def normalize_heading(heading: str) -> str:
    """比對標題用：去編號、去空白、轉小寫。"""
    return re.sub(r"\s+", "", strip_heading_number(heading)).lower()
