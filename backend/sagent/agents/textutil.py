"""整理模型輸出的小工具。小模型的輸出常常多一點、少一點，這裡負責收拾。"""

from __future__ import annotations

import json
import re
from typing import Any

from ..services.diagrams import (
    DIAGRAM_LANGS,
    extract_diagrams,
    normalize_diagram,
    strip_think,
    transform_diagrams,
)
from ..services.markdown_doc import iter_lines_with_fence_state, normalize_heading


def parse_json_object(text: str) -> dict[str, Any]:
    """解析 JSON 物件。模型前後多講話或包 ```json 時也能處理。"""
    text = strip_think(text).strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("找不到 JSON 物件") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 格式錯誤：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON 不是物件")
    return data


def clean_list(value: Any, limit: int = 20, max_len: int = 300) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text[:max_len])
    return out[:limit]


# 整段被 ``` 包住：```markdown 一定拆開；沒寫語言時，裡面沒有其他 ``` 才拆
# （避免把開頭和結尾剛好都是範例程式碼的章節拆壞）
_WRAP_RE = re.compile(r"^```(markdown|md)?[ \t]*\n(.*)\n```\s*$", re.S)
_FENCE_LINE_RE = re.compile(r"^\s{0,3}(```|~~~)", re.M)
# 模型自己放的圖片（圖是另外畫的，圖片檔不存在）
_IMAGE_LINE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
# 提示詞的內部說明外洩，例如「（註：圖表由其他步驟產生，此處僅描述內容）」
_DIAGRAM_NOTE_RE = re.compile(r"[（(]註[:：][^）)\n]*圖[^）)\n]*[）)]")


def clean_section_body(text: str, heading: str, *, keep_diagrams: bool) -> str:
    """整理模型寫出的章節內文：
    - 去掉 <think>、整段被 ```markdown 包住的情況
    - 去掉開頭重複的章節標題
    - 內文裡的 # / ## 標題降成 ###，避免把文件結構切亂
    - 不保留圖時，拿掉模型自己畫的圖
    - 拿掉模型自己放的圖片，以及「（註：圖表由其他步驟產生）」這類內部說明
    """
    text = strip_think(text).strip()
    m = _WRAP_RE.match(text)
    if m and (m.group(1) or not _FENCE_LINE_RE.search(m.group(2))):
        text = m.group(2).strip()
    lines = text.splitlines()
    # 去掉開頭的空行，以及跟章節標題一樣的標題行（例如「## 3. 業務流程」「**業務流程**」）
    target = normalize_heading(heading)
    while lines:
        first = lines[0].strip()
        if not first:
            lines.pop(0)
            continue
        candidate = re.sub(r"^#{1,6}\s+", "", first).strip("* ")
        if candidate != first and normalize_heading(candidate) == target:
            lines.pop(0)
            continue
        break
    text = "\n".join(lines)

    out: list[str] = []
    for line, in_fence in iter_lines_with_fence_state(text):
        if not in_fence:
            if _IMAGE_LINE_RE.match(line):
                continue
            line = _DIAGRAM_NOTE_RE.sub("", line)
            if re.match(r"^#{1,2}\s", line):
                line = "### " + line.lstrip("#").strip()
        out.append(line)
    text = "\n".join(out).strip()
    text = _normalize_subheadings(text)

    if keep_diagrams:
        text = fix_inline_diagrams(text)
    else:
        blocks = extract_diagrams(text)
        if blocks:
            lines = text.splitlines()
            drop = set()
            for b in blocks:
                drop.update(range(b.start, b.end + 1))
            text = "\n".join(ln for i, ln in enumerate(lines) if i not in drop).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def _normalize_subheadings(text: str) -> str:
    """小標題最上層統一成 ###。模型常直接用 ####，整份文件的層級會不一致。"""
    levels = [
        len(m.group(1))
        for line, in_fence in iter_lines_with_fence_state(text)
        if not in_fence and (m := re.match(r"^(#{3,6})\s", line))
    ]
    shift = min(levels) - 3 if levels else 0
    if shift <= 0:
        return text
    out = []
    for line, in_fence in iter_lines_with_fence_state(text):
        m = None if in_fence else re.match(r"^(#{3,6})(\s.*)$", line)
        out.append("#" * (len(m.group(1)) - shift) + m.group(2) if m else line)
    return "\n".join(out)


def fix_inline_diagrams(markdown: str) -> str:
    """文件裡已有的圖，套用自動修正（例如幫含括號的節點文字加引號）。"""

    def _fix(_idx, block):
        fixed, _errors = normalize_diagram(block.lang, block.code)
        return fixed if fixed != block.code else None

    return transform_diagrams(markdown, _fix)


_ITEM_RE = re.compile(r"\b([A-Z]{1,4}-\d{2,3})\b[\s|：:*]*([^\n|]{0,50})")


def extract_item_ids(text: str) -> list[str]:
    """抓出「FR-01 使用者可以登入」這類編號項目，讓後面的章節引用時編號一致。"""
    seen: dict[str, str] = {}
    for m in _ITEM_RE.finditer(text):
        item_id, desc = m.group(1), m.group(2).strip(" *-")
        if item_id not in seen:
            seen[item_id] = f"{item_id} {desc}".strip()
    return list(seen.values())


def normalize_sid(value: Any, prefix: str, count: int) -> str | None:
    """把「S2」「s2」「[S2]」「2」「第2節」都轉成「S2」。超出範圍回傳 None。"""
    m = re.search(r"(\d+)", str(value or ""))
    if not m:
        return None
    n = int(m.group(1))
    if 1 <= n <= count:
        return f"{prefix}{n}"
    return None


_MARKER_RE = re.compile(r"[ \t]*\[\[圖(\d+)\]\][ \t]*")


def diagrams_to_markers(body: str) -> tuple[str, list[str]]:
    """把內文中的圖換成 [[圖1]] 標記，修改文字時不會動到圖。回傳 (換過的內文, 原本的圖區塊)。"""
    blocks = extract_diagrams(body)
    if not blocks:
        return body, []
    lines = body.splitlines()
    out: list[str] = []
    originals: list[str] = []
    cursor = 0
    for i, b in enumerate(blocks, start=1):
        out.extend(lines[cursor : b.start])
        originals.append("\n".join(lines[b.start : b.end + 1]) + ("" if b.closed else f"\n{b.fence}"))
        out.append(f"[[圖{i}]]")
        cursor = b.end + 1
    out.extend(lines[cursor:])
    return "\n".join(out), originals


def markers_to_diagrams(text: str, originals: list[str]) -> str:
    used: set[int] = set()

    def _sub(m: re.Match) -> str:
        k = int(m.group(1))
        if 1 <= k <= len(originals) and k not in used:
            used.add(k)
            # 標記可能被寫在句子中間（「如下圖 [[圖1]]。」），圖一定要自己獨立成段
            return f"\n\n{originals[k - 1]}\n\n"
        return ""

    text = _MARKER_RE.sub(_sub, text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    missing = [blk for k, blk in enumerate(originals, start=1) if k not in used]
    if missing:
        text = text.rstrip() + "\n\n" + "\n\n".join(missing)
    return text


def text_length(body: str) -> int:
    """不含圖的文字長度。"""
    blocks = extract_diagrams(body)
    lines = body.splitlines()
    drop = set()
    for b in blocks:
        drop.update(range(b.start, b.end + 1))
    return len("".join(ln.strip() for i, ln in enumerate(lines) if i not in drop))


def is_bullet(line: str) -> bool:
    """是不是條列項目（- / * / + / 1. 開頭）。"""
    return bool(re.match(r"^\s*([-*+]|\d+[.)])\s+", line))


def bullet_text(item: str) -> str:
    return re.sub(r"^([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?", "", item.strip()).strip()


def is_diagram_lang(info: str) -> bool:
    return info.lower() in DIAGRAM_LANGS
