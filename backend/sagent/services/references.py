"""撰寫時參考同專案的其他 Spec（研究計劃 C-13）。

例如寫系統架構設計時參考功能規格書：沒有參考文件時，模型只看得到這份文件的需求，
前面文件已經定好的編號、狀態、角色只能自己猜或標待確認。

只放精簡的內容，不放全文（CPU 上提示詞越長越慢，也越容易超過上下文）：
- 編號項目（FR-01 使用者可以登入……），全部加起來最多 MAX_ITEMS 項
- 名詞（角色、狀態、資料），每份最多 MAX_TERMS 個

組好的文字放在 RunInput.references，提示詞用（參考文件開始）…（參考文件結束）包起來，寫明只是參考資料。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..agents.textutil import extract_item_ids
from ..models import Spec
from . import specs as spec_service
from .glossary import extract_terms

MAX_SPECS = 3
MAX_ITEMS = 40
MAX_TERMS = 20
_FENCE_RE = re.compile(r"```.*?```", re.S)


def build_references(
    s: Session,
    project_id: int,
    spec_ids: list[int],
    doc_type_name: Callable[[str], str] = lambda key: key,
) -> str:
    """參考文件的最新版整理成一段文字。不是這個專案的、已經刪掉的、沒有內容的跳過。"""
    blocks: list[str] = []
    budget = MAX_ITEMS
    for n, spec_id in enumerate(spec_ids[:MAX_SPECS], start=1):
        spec = s.get(Spec, spec_id)
        if spec is None or spec.project_id != project_id:
            continue
        head = spec_service.get_head(s, spec)
        if head is None or not head.content.strip():
            continue
        content = _FENCE_RE.sub("", head.content)
        items = extract_item_ids(content)[: max(budget, 0)]
        budget -= len(items)
        terms = extract_terms(content)[:MAX_TERMS]
        lines = [f"### 參考文件 {n}：{spec.title}（{doc_type_name(spec.doc_type)}，第 {head.version_no} 版）"]
        if items:
            lines.append("編號項目：")
            lines += [f"- {item}" for item in items]
        if terms:
            by_kind: dict[str, list[str]] = {}
            for t in terms:
                by_kind.setdefault(t["kind"], []).append(t["name"])
            lines.append("名詞：" + "；".join(f"{kind}：{'、'.join(names)}" for kind, names in by_kind.items()))
        if len(lines) > 1:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
