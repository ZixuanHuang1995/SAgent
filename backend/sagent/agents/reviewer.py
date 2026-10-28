"""Reviewer：找出格式和邏輯上的錯誤（交給 Writer 修正），以及需要人工覆核的地方（列給使用者）。

不評論寫得好不好、完不完整，也不打分數。
格式問題盡量用程式檢查（小模型數表格欄位、對編號都不可靠），模型只負責程式看不出來的邏輯問題。
"""

from __future__ import annotations

import re
from typing import Any

from ..services.diagrams import extract_diagrams, lint_mermaid, lint_plantuml
from .base import AgentBase
from .prompts import REVIEW_SCHEMA, reviewer_messages
from .textutil import normalize_sid, text_length
from .types import Draft, Issue, Plan, Review, ReviewPoint, RunInput

_PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME|Lorem ipsum)\b|（待補充）", re.I)
_SEVERITIES = ("high", "medium", "low")
_FENCE_RE = re.compile(r"```.*?```", re.S)
_ID_RE = re.compile(r"\b([A-Z]{1,4})-(\d{2,3})\b")
# 一行的開頭（條列、標題、粗體之後）或表格第一欄出現的編號，算是「定義」
_ID_DEF_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|#+\s+|\|\s*)?(?:\*\*|`)?([A-Z]{1,4}-\d{2,3})\b", re.M)
_MARK_RE = re.compile(r"[（(](假設|待確認)[）)]")
MAX_CHECK_POINTS = 20


def _without_code(text: str) -> str:
    return _FENCE_RE.sub("", text)


def _cells(row: str) -> int:
    row = row.strip().replace("\\|", "")
    return len(row.strip("|").split("|"))


def table_problems(body: str) -> list[str]:
    """Markdown 表格：每一列的欄位數要跟標題列一樣。"""
    problems: list[str] = []
    lines = _without_code(body).splitlines()
    i = 0
    table_no = 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            table_no += 1
            header = _cells(lines[i])
            bad = 0
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                if _cells(lines[j]) != header:
                    bad += 1
                j += 1
            if bad:
                problems.append(f"第 {table_no} 個表格有 {bad} 列的欄位數跟標題列（{header} 欄）不一樣")
            i = j
        else:
            i += 1
    return problems


def undefined_ids(draft: Draft) -> dict[str, list[str]]:
    """引用了整份文件都沒有定義的編號，回傳 {章節編號: [編號, …]}。

    只看文件裡有定義過的編號種類（例如有定義 FR-01，才檢查 FR-07 有沒有定義），
    避免把需求裡提到的外部編號當成錯誤。
    """
    defined: set[str] = set()
    for sec in draft.sections:
        defined.update(_ID_DEF_RE.findall(_without_code(sec.body)))
    prefixes = {d.split("-")[0] for d in defined}
    result: dict[str, list[str]] = {}
    for sec in draft.sections:
        missing = []
        for prefix, number in _ID_RE.findall(_without_code(sec.body)):
            item = f"{prefix}-{number}"
            if prefix in prefixes and item not in defined and item not in missing:
                missing.append(item)
        if missing:
            result[sec.id] = missing
    return result


def has_item_ids(body: str, prefix: str) -> bool:
    """這一節有沒有定義範本指定的編號（例如功能需求一節要有 FR-01）。"""
    return any(d.split("-")[0] == prefix for d in _ID_DEF_RE.findall(_without_code(body)))


def static_checks(plan: Plan, draft: Draft) -> list[Issue]:
    issues: list[Issue] = []
    undefined = undefined_ids(draft)
    for sec in draft.sections:
        sec_plan = plan.section(sec.id)
        if sec_plan is not None and sec_plan.action == "keep":
            continue
        if text_length(sec.body) < 30:
            issues.append(Issue(sec.id, "high", "這一節內容太少", "依大綱的目標補上具體內容", source="check"))
        for idx, block in enumerate(extract_diagrams(sec.body)):
            errors = lint_plantuml(block.code) if block.lang == "plantuml" else lint_mermaid(block.code)
            if errors:
                issues.append(
                    Issue(
                        sec.id,
                        "high",
                        f"第 {idx + 1} 張圖語法錯誤：{'；'.join(errors[:3])}",
                        "照語法規則修正",
                        source="check",
                        diagram_index=idx,
                    )
                )
        if sec_plan is not None and sec_plan.ids and not has_item_ids(sec.body, sec_plan.ids):
            p = sec_plan.ids
            issues.append(
                Issue(
                    sec.id,
                    "medium",
                    f"這一節的項目要用 {p}-01、{p}-02… 編號，但沒有找到",
                    f"每個項目的開頭（條列開頭或表格第一欄）加上編號 {p}-01、{p}-02…",
                    source="check",
                )
            )
        for problem in table_problems(sec.body):
            issues.append(Issue(sec.id, "medium", problem, "補齊或合併欄位，讓每一列的欄位數一致", source="check"))
        if sec.id in undefined:
            ids = "、".join(undefined[sec.id][:5])
            issues.append(
                Issue(
                    sec.id,
                    "medium",
                    f"引用了文件裡沒有定義的編號：{ids}",
                    "改成前面章節已經定義的編號，或刪除這個引用",
                    source="check",
                )
            )
        if _PLACEHOLDER_RE.search(sec.body):
            issues.append(
                Issue(
                    sec.id,
                    "low",
                    "內文有未完成的標記（TODO、待補充等）",
                    "補上內容或移除標記",
                    source="check",
                )
            )
    return issues


def _plain(line: str) -> str:
    """去掉 Markdown 記號，留下給人看的文字。"""
    text = re.sub(r"^\s*(?:[-*+]\s+(?:\[[ x]\]\s+)?|\d+[.)]\s+|#+\s+|>\s*)", "", line)
    text = text.replace("**", "").replace("`", "")
    if text.strip().startswith("|"):
        text = " / ".join(c.strip() for c in text.strip().strip("|").split("|") if c.strip())
    return re.sub(r"\s+", " ", text).strip()


def marked_points(draft: Draft) -> list[ReviewPoint]:
    """文件裡標註（假設）、（待確認）的地方，都要人工確認。"""
    points: list[ReviewPoint] = []
    seen: set[str] = set()
    for sec in draft.sections:
        for line in _without_code(sec.body).splitlines():
            m = _MARK_RE.search(line)
            if not m:
                continue
            text = _plain(line)
            if not text or text in seen:
                continue
            seen.add(text)
            reason = "文件標註為假設，內容要確認是否正確" if m.group(1) == "假設" else "文件標註為待確認"
            points.append(ReviewPoint(sec.id, text[:120] + ("…" if len(text) > 120 else ""), reason, "check"))
            if len(points) >= MAX_CHECK_POINTS:
                return points
    return points


def draft_for_review(plan: Plan, draft: Draft, only: set[str] | None = None) -> str:
    """給審查模型看的草稿：圖表程式碼換成一行說明，節省 token。only 有值時只放這些章節。"""
    parts = []
    for number, sec in enumerate(draft.sections, start=1):
        if only is not None and sec.id not in only:
            continue
        body = sec.body
        for block in reversed(extract_diagrams(body)):
            lines = body.splitlines()
            first = block.code.strip().splitlines()[0] if block.code.strip() else ""
            lines[block.start : block.end + 1] = [f"（{block.lang} 圖：{first[:40]}）"]
            body = "\n".join(lines)
        parts.append(f"[{sec.id}] ## {number}. {sec.heading}\n{body.strip()}")
    return "\n\n".join(parts)


def parse_llm_issues(data: dict[str, Any], plan: Plan) -> list[Issue]:
    issues: list[Issue] = []
    seen: set[tuple[str | None, str]] = set()
    for raw in data.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        problem = str(raw.get("problem", "")).strip()
        if not problem:
            continue
        severity = str(raw.get("severity", "medium")).strip().lower()
        if severity not in _SEVERITIES:
            severity = "medium"
        sid = normalize_sid(raw.get("section_id"), "S", len(plan.sections))
        key = (sid, problem)
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            Issue(sid, severity, problem[:400], str(raw.get("suggestion", "")).strip()[:400], source="llm")  # type: ignore[arg-type]
        )
    return issues[:12]


def parse_llm_points(data: dict[str, Any], plan: Plan) -> list[ReviewPoint]:
    points: list[ReviewPoint] = []
    seen: set[str] = set()
    for raw in data.get("review_points") or []:
        if not isinstance(raw, dict):
            continue
        item = str(raw.get("item", "")).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        sid = normalize_sid(raw.get("section_id"), "S", len(plan.sections))
        points.append(ReviewPoint(sid, item[:300], str(raw.get("reason", "")).strip()[:300], "llm"))
    return points[:10]


class Reviewer(AgentBase):
    role = "reviewer"

    async def review(
        self,
        inp: RunInput,
        plan: Plan,
        draft: Draft,
        round_no: int,
        focus: set[str] | None = None,
        previous: Review | None = None,
    ) -> Review:
        """focus 有值時（修訂後的再審查）只請模型看修改過的章節，其他章節沿用上一輪的意見。

        CPU 上讀整份草稿要好幾分鐘，只看修改過的章節可以省下大部分時間。
        """
        step = f"review:{round_no}"
        label = (
            "審查文件"
            if round_no == 1
            else f"第 {round_no} 次審查（只看修改過的章節）"
            if focus
            else f"第 {round_no} 次審查"
        )
        async with self.step(step, label) as info:
            checks = static_checks(plan, draft)
            marked = marked_points(draft)
            notes = [f"[{i.section_id}] {i.problem}" for i in checks if i.severity != "low"]
            data = await self.chat_json(
                step,
                reviewer_messages(
                    inp, plan, draft_for_review(plan, draft, focus), notes, partial=bool(focus)
                ),
                REVIEW_SCHEMA,
                temperature=0.1,
                max_tokens=2000,
            )
            carried: list[Issue] = []
            carried_points: list[ReviewPoint] = []
            if focus and previous is not None:
                carried = [i for i in previous.issues if i.source == "llm" and i.section_id not in focus]
                carried_points = [
                    p for p in previous.review_points if p.source == "llm" and p.section_id not in focus
                ]
            if data is None:
                review = Review(
                    round=round_no,
                    summary="審查模型沒有給出可用的結果，只列出程式檢查的結果。",
                    issues=checks + carried,
                    review_points=marked + carried_points,
                    llm_ok=False,
                )
            else:
                new_issues = parse_llm_issues(data, plan)
                new_points = parse_llm_points(data, plan)
                if focus:
                    new_issues = [i for i in new_issues if i.section_id is None or i.section_id in focus]
                    new_points = [p for p in new_points if p.section_id is None or p.section_id in focus]
                review = Review(
                    round=round_no,
                    summary=str(data.get("summary", "")).strip()[:500],
                    issues=checks + new_issues + carried,
                    review_points=marked + new_points + carried_points,
                )
            info.output = review.to_dict()
            errors = sum(1 for i in review.issues if i.severity in ("high", "medium"))
            verdict = f"需要修訂（錯誤 {errors} 個）" if review.needs_revision else "沒有需要修正的錯誤"
            info.message = f"審查結果：{verdict}，需要人工覆核 {len(review.review_points)} 項"
        return review
