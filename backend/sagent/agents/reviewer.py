"""Reviewer：找出格式和邏輯上的錯誤（交給 Writer 修正），以及需要人工覆核的地方（列給使用者）。

不評論寫得好不好、完不完整，也不打分數。
格式問題盡量用程式檢查（小模型數表格欄位、對編號都不可靠），模型只負責程式看不出來的邏輯問題。
"""

from __future__ import annotations

import re
from typing import Any

from ..services.diagrams import extract_diagrams, lint_mermaid, lint_plantuml
from .base import AgentBase
from .prompts import ISSUE_KINDS, review_schema, reviewer_messages
from .textutil import normalize_sid, text_length
from .types import Draft, Issue, Plan, Review, ReviewPoint, RunInput

_PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME|Lorem ipsum)\b|（待補充）", re.I)
_SEVERITIES = ("high", "medium", "low")
_FENCE_RE = re.compile(r"```.*?```", re.S)
_ID_RE = re.compile(r"\b([A-Z]{1,4})-(\d{2,3})\b")
# 一行的開頭（條列、標題、粗體之後）或表格第一欄出現的編號，算是「定義」
_ID_DEF_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|#+\s+|\|\s*)?(?:\*\*|`)?([A-Z]{1,4}-\d{2,3})\b", re.M)
_ID_CELL_RE = re.compile(r"(?:\*\*|`)?([A-Z]{1,4}-\d{2,3})(?:\*\*|`)?")
_MARK_RE = re.compile(r"[（(](假設|待確認)[）)]")
_QUOTED_RE = re.compile(r"「([^」]{2,30})」")
MAX_CHECK_POINTS = 20
_UNSTATED_REASON = "需求沒提到，可能是撰寫者自己補的，要確認是否正確"
_STALE_REASON = "上一輪審查指出的問題，修改後沒有重新審查成功，要確認是否已經修正"


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


def _table_cells(line: str) -> list[str] | None:
    """表格的一列拆成格子；不是表格或是分隔列（|---|）回傳 None。"""
    s = line.strip()
    if not s.startswith("|") or re.fullmatch(r"[|\s:\-]+", s):
        return None
    return [c.strip() for c in s.replace("\\|", "").strip("|").split("|")]


def _id_cell(cell: str) -> str | None:
    m = _ID_CELL_RE.fullmatch(cell)
    return m.group(1) if m else None


def defined_ids(text: str) -> list[str]:
    """文件裡「定義」的編號：條列、標題、一行開頭的編號，或表格一列裡第一個只寫編號的格子。

    表格的編號不一定在第一欄，例如「| 報修人 | UC-01 | 提交報修申請 |」。
    """
    ids = _ID_DEF_RE.findall(text)
    for line in text.splitlines():
        cells = _table_cells(line)
        found = next((i for i in map(_id_cell, cells or []) if i), None)
        if found:
            ids.append(found)
    return ids


def _phrase(rest: str) -> str:
    """編號後面接的名稱，例如「[UC-04] 審核與退回報修單。」的「審核與退回報修單」。"""
    rest = rest.lstrip(" \t*`]）)：:|-–")
    return re.split(r"[。，,；;（(|\n*`]", rest, maxsplit=1)[0].strip()[:30]


def _similar(a: str, b: str) -> bool:
    if a == b or (min(len(a), len(b)) >= 3 and (a in b or b in a)):
        return True
    x, y = {a[i : i + 2] for i in range(len(a) - 1)}, {b[i : i + 2] for i in range(len(b) - 1)}
    return bool(x and y) and 2 * len(x & y) / (len(x) + len(y)) >= 0.8


def id_titles(draft: Draft) -> dict[str, str]:
    """每個編號第一次定義時的名稱，例如 {"UC-04": "查看統計報表"}。"""
    titles: dict[str, str] = {}
    for sec in draft.sections:
        for line in _without_code(sec.body).splitlines():
            cells = _table_cells(line)
            if cells is not None:
                for k, cell in enumerate(cells):
                    item_id = _id_cell(cell)
                    if item_id:
                        if k + 1 < len(cells):
                            titles.setdefault(item_id, _phrase(cells[k + 1]))
                        break
                continue
            m = _ID_DEF_RE.match(line)
            if m:
                titles.setdefault(m.group(1), _phrase(line[m.end() :]))
    return {k: v for k, v in titles.items() if len(v) >= 2}


def mismatched_ids(draft: Draft) -> dict[str, list[str]]:
    """引用編號時寫的名稱，其實是另一個編號的名稱（例如寫「[UC-04] 審核與退回」，但審核與退回是 UC-02）。

    小模型寫後面的章節時常把編號對錯，真模型實測這是最常見的邏輯錯誤。
    只在名稱跟另一個同種編號的名稱一樣、又跟自己的名稱不像時才算，避免誤判。
    """
    titles = id_titles(draft)
    result: dict[str, list[str]] = {}
    for sec in draft.sections:
        found: list[str] = []
        for line in _without_code(sec.body).splitlines():
            for m in _ID_RE.finditer(line):
                item_id, phrase = m.group(0), _phrase(line[m.end() :])
                own = titles.get(item_id, "")
                if len(phrase) < 2 or (own and _similar(phrase, own)):
                    continue
                others = [
                    other
                    for other, title in titles.items()
                    if other != item_id and other.split("-")[0] == m.group(1) and _similar(phrase, title)
                ]
                if len(others) != 1:
                    continue
                note = f"（{item_id} 是「{own}」）" if own else f"（{item_id} 沒有定義）"
                text = f"{item_id} 後面寫「{phrase}」，這是 {others[0]} 的名稱{note}"
                if text not in found:
                    found.append(text)
        if found:
            result[sec.id] = found
    return result


def undefined_ids(draft: Draft) -> dict[str, list[str]]:
    """引用了整份文件都沒有定義的編號，回傳 {章節編號: [編號, …]}。

    只看文件裡有定義過的編號種類（例如有定義 FR-01，才檢查 FR-07 有沒有定義），
    避免把需求裡提到的外部編號當成錯誤。
    """
    defined: set[str] = set()
    for sec in draft.sections:
        defined.update(defined_ids(_without_code(sec.body)))
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
    return any(d.split("-")[0] == prefix for d in defined_ids(_without_code(body)))


def static_checks(plan: Plan, draft: Draft) -> list[Issue]:
    issues: list[Issue] = []
    undefined = undefined_ids(draft)
    mismatched = mismatched_ids(draft)
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
        for problem in mismatched.get(sec.id, [])[:5]:
            issues.append(
                Issue(
                    sec.id,
                    "medium",
                    f"引用的編號跟名稱對不上：{problem}",
                    "改成名稱對應的編號，或改成這個編號定義的名稱",
                    source="check",
                    kind="conflict",
                )
            )
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


_CJK = "[一-鿿]"
# 需求裡帶單位的數字（「3 天」「5 個工作天」「5MB」），或三位數以上的數字（「409」）
_FACT_RE = re.compile(
    r"(?<![\d/.-])(\d+(?:\.\d+)?)\s*(個工作天|工作天|個月|小時|分鐘|天|週|年|次|張|件|筆|MB|GB|KB|%|秒|字元)?(?![\d/])"
)
MAX_GAP_POINTS = 5
_NUMBER_GAP_REASON = "文件裡找不到這個數字，要確認有沒有漏寫或寫錯"
_ITEM_GAP_REASON = "文件裡幾乎找不到對應的內容，要確認有沒有漏寫"


def requirement_items(requirement: str) -> list[str]:
    """需求拆成一句一句：每行去掉條列記號，長的再依句號、分號拆開。太短的和結尾是「：」的引言不算。"""
    items: list[str] = []
    for line in requirement.splitlines():
        if line.lstrip().startswith("#"):
            continue
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)、]|[（(]\d+[）)])\s*", "", line).strip()
        for part in re.split(r"(?<=[。；;！？])", line):
            part = part.strip()
            if len(re.findall(_CJK, part)) >= 8 and not part.endswith(("：", ":")):
                items.append(part)
    return items


def _grams(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text)
    grams = {text[i : i + 2] for i in range(len(text) - 1) if re.fullmatch(_CJK + "{2}", text[i : i + 2])}
    return grams | {w.lower() for w in re.findall(r"[A-Za-z0-9]{2,}", text)}


def requirement_gaps(requirement: str, draft: Draft) -> list[ReviewPoint]:
    """需求有、文件裡卻找不到的內容，列給使用者確認有沒有漏寫（D4：每條需求都要追溯得到）。

    兩種情況：需求寫的數字（帶單位，例如「3 天」）文件裡沒有；或某一句需求跟文件幾乎沒有共同的字詞。
    只是粗略的比對，換句話說也可能被當成沒寫到，所以只列人工覆核，不交給 Writer 修。
    """
    doc = _without_code("\n".join(sec.body for sec in draft.sections))
    flat, doc_grams = re.sub(r"\s+", "", doc), _grams(doc)
    points: list[ReviewPoint] = []
    seen: set[str] = set()
    for m in _FACT_RE.finditer(requirement):
        number, unit = m.group(1), m.group(2) or ""
        key = number + unit
        if (not unit and len(number) < 3) or key in seen:
            continue
        # 數字和單位中間可以夾幾個字，例如需求「3 天」、文件寫「3 個日曆天」
        if re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])[^\d]{{0,4}}{re.escape(unit[-1:])}", flat):
            continue
        seen.add(key)
        points.append(ReviewPoint(None, f"需求提到「{m.group(0).strip()}」", _NUMBER_GAP_REASON, "check"))
    for item in requirement_items(requirement):
        grams = _grams(item)
        if grams and len(grams & doc_grams) / len(grams) < 0.3:
            points.append(ReviewPoint(None, f"需求：{item[:80]}", _ITEM_GAP_REASON, "check"))
    return points[:MAX_GAP_POINTS]


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


def _is_unstated(raw: dict[str, Any]) -> bool:
    return str(raw.get("kind", "")).strip().lower() == "unstated"


def parse_llm_issues(data: dict[str, Any], plan: Plan) -> list[Issue]:
    """模型標成 unstated（需求沒提到）的不算錯誤，由 parse_llm_points 轉成人工覆核。"""
    issues: list[Issue] = []
    seen: set[tuple[str | None, str]] = set()
    for raw in data.get("issues") or []:
        if not isinstance(raw, dict) or _is_unstated(raw):
            continue
        problem = str(raw.get("problem", "")).strip()
        if not problem:
            continue
        kind = str(raw.get("kind", "")).strip().lower()
        severity = str(raw.get("severity", "medium")).strip().lower()
        if severity not in _SEVERITIES:
            severity = "medium"
        sid = normalize_sid(raw.get("section_id"), "S", len(plan.sections))
        key = (sid, problem)
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            Issue(
                sid,
                severity,  # type: ignore[arg-type]
                problem[:400],
                str(raw.get("suggestion", "")).strip()[:400],
                source="llm",
                kind=kind if kind in ISSUE_KINDS else "",
            )
        )
    return issues[:12]


def parse_llm_points(data: dict[str, Any], plan: Plan) -> list[ReviewPoint]:
    """review_points，加上 issues 裡標成 unstated 的（照設計要人工覆核，不交給 Writer 修）。"""
    raw_points: list[tuple[Any, str, str, bool]] = []
    for raw in data.get("review_points") or []:
        if isinstance(raw, dict):
            raw_points.append(
                (raw.get("section_id"), str(raw.get("item", "")), str(raw.get("reason", "")), False)
            )
    for raw in data.get("issues") or []:
        if isinstance(raw, dict) and _is_unstated(raw):
            raw_points.append((raw.get("section_id"), str(raw.get("problem", "")), _UNSTATED_REASON, True))
    points: list[ReviewPoint] = []
    seen: set[str] = set()
    for section_id, item, reason, from_issue in raw_points:
        item = item.strip()
        if not item or item in seen:
            continue
        sid = normalize_sid(section_id, "S", len(plan.sections))
        # 模型常在 review_points 和 issues 用不同的話講同一件事；同一節引號裡的詞一樣就算重複
        if from_issue and any(p.section_id == sid and _quoted(p.item) & _quoted(item) for p in points):
            continue
        seen.add(item)
        points.append(ReviewPoint(sid, item[:300], reason.strip()[:300], "llm"))
    return points[:12]


def _quoted(text: str) -> set[str]:
    return set(_QUOTED_RE.findall(text))


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
            if inp.mode == "draft":  # 修訂模式的需求是要改什麼，不用每一點都在文件裡找得到
                marked += requirement_gaps(inp.requirement, draft)
            notes = [f"[{i.section_id}] {i.problem}" for i in checks if i.severity != "low"]
            data = await self.chat_json(
                step,
                reviewer_messages(
                    inp, plan, draft_for_review(plan, draft, focus), notes, partial=bool(focus)
                ),
                review_schema(partial=bool(focus)),
                temperature=0.1,
                # 真模型實測審查輸出平均約 1500 tokens，2000 常被截斷
                max_tokens=3000,
            )
            carried: list[Issue] = []
            carried_points: list[ReviewPoint] = []
            if focus and previous is not None:
                carried = [i for i in previous.issues if i.source == "llm" and i.section_id not in focus]
                carried_points = [
                    p for p in previous.review_points if p.source == "llm" and p.section_id not in focus
                ]
            if data is None:
                summary = "審查模型沒有給出可用的結果，只列出程式檢查的結果。"
                stale: list[ReviewPoint] = []
                if focus and previous is not None:
                    # 修改過的章節沒能重新審查：上一輪的意見不能當成已經修好，改列人工覆核。
                    # 不放回 issues，避免依舊的意見再修一次
                    stale = [
                        ReviewPoint(i.section_id, i.problem, _STALE_REASON, "llm")
                        for i in previous.issues
                        if i.source == "llm" and i.section_id in focus and i.severity in ("high", "medium")
                    ]
                    stale += [p for p in previous.review_points if p.source == "llm" and p.section_id in focus]
                    if stale:
                        summary += "修改過的章節上一輪指出的問題，改列人工覆核。"
                review = Review(
                    round=round_no,
                    summary=summary,
                    issues=checks + carried,
                    review_points=marked + carried_points + stale,
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
            if not review.llm_ok:
                verdict = f"審查模型沒有結果，只有程式檢查（錯誤 {errors} 個）"
            info.message = f"審查結果：{verdict}，需要人工覆核 {len(review.review_points)} 項"
        return review
