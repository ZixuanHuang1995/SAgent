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
from .textutil import copied_ratio, normalize_sid, substantive_chars, text_length
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
# 第一輪審查分批（C-14）：估計的輸入加上輸出上限，超過上下文長度的這個比例就分批
REVIEW_MAX_TOKENS = 3000
CONTEXT_SHARE = 0.85
_BATCH_FAILED_REASON = "文件太長分批審查時，這一批審查模型沒有給出結果，要人工看過"
# 空洞、照抄的門檻（C-01）。用 scripts/speceval/controls.py 校正：22 份示範文件都不會被擋，
# 「每節只寫待確認加編號」「需求原文加編號」兩種假文件都會被擋
MIN_ITEMS_FOR_HOLLOW = 3  # 需求至少有幾句完整的句子，才檢查空洞
MIN_SUBSTANTIVE_CHARS = 30
MIN_COPY_CHARS = 80  # 太短的章節（例如一句目的）照抄需求是正常的
MAX_COPIED_RATIO = 0.8  # 示範文件最高 0.66（變更原因本來就會重述需求），照抄的假文件約 0.9
_UNSTATED_REASON = "需求沒提到，可能是撰寫者自己補的，要確認是否正確"
_STALE_REASON = "上一輪審查指出的問題，修改後沒有重新審查成功，要確認是否已經修正"
_KEEP_REASON = "這一節設定為保留原內容，不會自動修改，要確認是否需要調整"
_NO_SECTION_REASON = "審查沒有指出是哪一節，不會自動修改，要人工確認"
_LATE_REASON = "修改後再審查才找到的次要問題，不再自動修改，要確認是否需要處理"
# 小模型常把「需求沒提到」標成 requirement（跟需求矛盾）。關鍵字前面是文件、章節時，
# 意思是文件漏寫了需求要的東西，那是真的要修，不算
_UNSTATED_HINT_RE = re.compile(
    # 「未在需求中提及」「沒有在需求裡提到」也算（真模型實測的寫法）
    r"未(?:在需求[中裡內]?)?(?:提及|提到)|沒(?:有)?(?:在需求[中裡內]?)?(?:提及|提到)"
    r"|未要求|沒有要求|未指定|沒有指定|超出範圍|超出需求"
)
_DOC_SUBJECT_RE = re.compile(r"文件|章節|內文|草稿|S\d")


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


_LIST_JOIN_RE = re.compile(r"^\s*[*`\]]*\s*(?:[～~、/]|和|及|與|至|到|或)")


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
                if _LIST_JOIN_RE.match(line[m.end() :]):
                    continue  # 「TC-05～TC-08」「TC-11 和 TC-12」是範圍、並列，後面不是這個編號的名稱
                item_id, phrase = m.group(0), _phrase(line[m.end() :])
                own = titles.get(item_id, "")
                if len(phrase) < 2 or (own and _similar(phrase, own)):
                    continue
                # 另一個編號的名稱本來就提到這個編號（「TC-14：TC-05～TC-08 產生的每一題」），不算對錯
                others = [
                    other
                    for other, title in titles.items()
                    if other != item_id
                    and other.split("-")[0] == m.group(1)
                    and item_id not in title
                    and _similar(phrase, title)
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


def static_checks(plan: Plan, draft: Draft, requirement: str = "") -> list[Issue]:
    """requirement：撰寫模式才傳（修訂模式的需求是「要改什麼」，不拿來比對照抄和空洞）。"""
    issues: list[Issue] = []
    undefined = undefined_ids(draft)
    mismatched = mismatched_ids(draft)
    # 需求只有一兩句時，大部分章節本來就只能標待確認，不算空洞（構想文件常這樣）
    rich_requirement = len(requirement_items(requirement)) >= MIN_ITEMS_FOR_HOLLOW if requirement else False
    for sec in draft.sections:
        sec_plan = plan.section(sec.id)
        if sec_plan is not None and sec_plan.action == "keep":
            continue
        if text_length(sec.body) < 30:
            issues.append(Issue(sec.id, "high", "這一節內容太少", "依大綱的目標補上具體內容", source="check"))
        elif rich_requirement and substantive_chars(sec.body) < MIN_SUBSTANTIVE_CHARS:
            issues.append(
                Issue(
                    sec.id,
                    "high",
                    "這一節幾乎都是待確認或編號，沒有實質內容",
                    "依需求寫出這一節能確定的內容；只有需求真的沒寫的事項才標（待確認）",
                    source="check",
                )
            )
        if requirement and text_length(sec.body) >= MIN_COPY_CHARS:
            ratio = copied_ratio(sec.body, requirement)
            if ratio >= MAX_COPIED_RATIO:
                issues.append(
                    Issue(
                        sec.id,
                        "medium",
                        f"這一節約 {ratio:.0%} 是照抄需求原文",
                        "依這一節的目標整理需求：拆成編號項目、表格或規則，不要整段貼上需求",
                        source="check",
                    )
                )
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
NUMBER_GAP_REASON = "文件裡找不到這個數字，要確認有沒有漏寫或寫錯"
ITEM_GAP_REASON = "文件裡幾乎找不到對應的內容，要確認有沒有漏寫"


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
        points.append(ReviewPoint(None, f"需求提到「{m.group(0).strip()}」", NUMBER_GAP_REASON, "check"))
    for item in requirement_items(requirement):
        grams = _grams(item)
        if grams and len(grams & doc_grams) / len(grams) < 0.3:
            points.append(ReviewPoint(None, f"需求：{item[:80]}", ITEM_GAP_REASON, "check"))
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
    """給審查模型看的草稿：圖表程式碼換成一行說明，節省 token。only 有值時只放這些章節。

    說明要寫清楚「有圖、只是省略程式碼」：只寫「（mermaid 圖：flowchart TD）」時，
    真模型以為圖沒有內容，每一輪都列「沒有提供 mermaid 程式碼」，Writer 怎麼改都消不掉。
    """
    parts = []
    for number, sec in enumerate(draft.sections, start=1):
        if only is not None and sec.id not in only:
            continue
        body = sec.body
        for block in reversed(extract_diagrams(body)):
            lines = body.splitlines()
            first = block.code.strip().splitlines()[0] if block.code.strip() else ""
            lines[block.start : block.end + 1] = [
                f"（這裡有一張 {block.lang} 圖，第一行是「{first[:40]}」；審查時省略圖的程式碼）"
            ]
            body = "\n".join(lines)
        parts.append(f"[{sec.id}] ## {number}. {sec.heading}\n{body.strip()}")
    return "\n\n".join(parts)


def says_unstated(problem: str) -> bool:
    """問題描述的意思是「需求沒提到，文件卻寫了」。"""
    for m in _UNSTATED_HINT_RE.finditer(problem):
        if not _DOC_SUBJECT_RE.search(problem[max(0, m.start() - 8) : m.start()]):
            return True
    return False


def _is_unstated(raw: dict[str, Any]) -> bool:
    kind = str(raw.get("kind", "")).strip().lower()
    if kind == "unstated":
        return True
    return kind == "requirement" and says_unstated(str(raw.get("problem", "")))


def _point_reason(raw: dict[str, Any], plan: Plan) -> str | None:
    """issues 裡 Writer 不會修的，回傳改列人工覆核的理由；要交給 Writer 修的回傳 None。

    Writer 不會修的問題留在 issues 裡，會一直觸發修訂、文件也永遠「需要修訂」。
    """
    if _is_unstated(raw):
        return _UNSTATED_REASON
    sid = normalize_sid(raw.get("section_id"), "S", len(plan.sections))
    sec = plan.section(sid) if sid else None
    if sec is None:
        return _NO_SECTION_REASON
    if sec.action == "keep":
        return _KEEP_REASON
    return None


def parse_llm_issues(data: dict[str, Any], plan: Plan) -> list[Issue]:
    """要交給 Writer 修的錯誤。需求沒提到的、保留的章節、沒指出章節的，由 parse_llm_points 轉成人工覆核。"""
    issues: list[Issue] = []
    seen: set[tuple[str | None, str]] = set()
    for raw in data.get("issues") or []:
        if not isinstance(raw, dict) or _point_reason(raw, plan) is not None:
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
    """review_points，加上 issues 裡 Writer 不會修的（需求沒提到、保留的章節、沒指出章節）。"""
    raw_points: list[tuple[Any, str, str, bool]] = []
    for raw in data.get("review_points") or []:
        if isinstance(raw, dict):
            raw_points.append(
                (raw.get("section_id"), str(raw.get("item", "")), str(raw.get("reason", "")), False)
            )
    for raw in data.get("issues") or []:
        reason = _point_reason(raw, plan) if isinstance(raw, dict) else None
        if reason is not None:
            raw_points.append((raw.get("section_id"), str(raw.get("problem", "")), reason, True))
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
        再審查時把上一輪對這些章節的意見交給模型，請它標出哪些已經修正（resolved）。
        沒修正的照原樣留著，新問題只收 high：不這樣做的話，每一輪都會找出一批新問題，修訂收不了尾。
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
            checks = static_checks(plan, draft, inp.requirement if inp.mode == "draft" else "")
            marked = marked_points(draft)
            if inp.mode == "draft":  # 修訂模式的需求是要改什麼，不用每一點都在文件裡找得到
                marked += requirement_gaps(inp.requirement, draft)
            notes = [f"[{i.section_id}] {i.problem}" for i in checks if i.severity != "low"]
            ledger: list[Issue] = []
            if focus and previous is not None:
                ledger = [
                    i
                    for i in previous.issues
                    if i.source == "llm" and i.section_id in focus and i.severity in ("high", "medium")
                ]
            batches = None if focus else await self._batches(inp, plan, draft, checks, notes)
            batch_failed: list[str] = []
            if batches:
                data, batch_failed = await self._review_batches(step, inp, plan, draft, checks, batches)
            else:
                data = await self.chat_json(
                    step,
                    reviewer_messages(
                        inp,
                        plan,
                        draft_for_review(plan, draft, focus),
                        notes,
                        partial=bool(focus),
                        previous=[f"[{i.section_id}] {i.problem}" for i in ledger],
                    ),
                    review_schema(partial=bool(focus), resolved=len(ledger)),
                    temperature=0.1,
                    # 真模型實測審查輸出平均約 1500 tokens，2000 常被截斷
                    max_tokens=REVIEW_MAX_TOKENS,
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
                new_issues = drop_check_duplicates(parse_llm_issues(data, plan), checks)
                new_points = parse_llm_points(data, plan)
                still_open: list[Issue] = []
                resolved: list[str] = []
                if focus:
                    new_issues = [i for i in new_issues if i.section_id is None or i.section_id in focus]
                    new_points = [p for p in new_points if p.section_id is None or p.section_id in focus]
                    still_open, resolved, new_issues = _apply_ledger(
                        ledger, resolved_numbers(data, len(ledger)), new_issues
                    )
                    # 修改後才找到的次要問題列給人看，不再觸發修訂
                    late = [i for i in new_issues if i.severity == "medium"]
                    new_issues = [i for i in new_issues if i.severity != "medium"]
                    new_points += [ReviewPoint(i.section_id, i.problem, _LATE_REASON, "llm") for i in late]
                if batch_failed:
                    new_points.append(
                        ReviewPoint(None, f"沒有審查到的章節：{'、'.join(batch_failed)}", _BATCH_FAILED_REASON, "llm")
                    )
                review = Review(
                    round=round_no,
                    summary=str(data.get("summary", "")).strip()[:500],
                    issues=checks + still_open + new_issues + carried,
                    review_points=marked + new_points + carried_points,
                    resolved=resolved,
                    llm_ok=not batch_failed,
                )
            info.output = review.to_dict()
            if batches:
                info.output["batches"] = [sorted(b, key=_section_order(draft)) for b in batches]
            errors = review.error_count
            verdict = f"需要修訂（錯誤 {errors} 個）" if review.needs_revision else "沒有需要修正的錯誤"
            if not review.llm_ok:
                verdict = f"審查模型沒有結果，只有程式檢查（錯誤 {errors} 個）"
            fixed = f"，上一輪的意見已修正 {len(review.resolved)} 個" if ledger and review.llm_ok else ""
            info.message = f"審查結果：{verdict}{fixed}，需要人工覆核 {len(review.review_points)} 項"
        return review


    async def _context_limit(self) -> int:
        get = getattr(self.llm, "context_length", None)
        if get is None:
            return 0
        try:
            return int(await get() or 0)
        except Exception:  # noqa: BLE001  查不到上下文長度就不分批
            return 0

    async def _batches(
        self, inp: RunInput, plan: Plan, draft: Draft, checks: list[Issue], notes: list[str]
    ) -> list[set[str]] | None:
        """第一輪審查要不要分批（C-14，筆記 D19）。不用分批時回傳 None。

        估計輸入的 token 數（字元數 × 這個工作實際的比例）加上輸出上限，超過上下文長度的 85% 就把章節
        照順序分成幾批，每一批都帶完整的大綱、名詞表和那一批章節的程式檢查結果。
        分批後模型看不到不同批之間的矛盾，所以只在真的放不下時才分。上下文長度不知道時不分批。
        """
        limit = await self._context_limit()
        if limit <= 0:
            return None
        budget = int(limit * CONTEXT_SHARE) - REVIEW_MAX_TOKENS
        model = self.llm.model

        def cost(sections: set[str]) -> int:
            batch_notes = [n for n in notes if any(n.startswith(f"[{s}]") for s in sections)]
            messages = reviewer_messages(inp, plan, draft_for_review(plan, draft, sections), batch_notes, batch=(9, 9))
            return self.token_estimate.tokens(model, sum(len(m["content"]) for m in messages))

        all_ids = [s.id for s in draft.sections]
        whole = self.token_estimate.tokens(
            model,
            sum(len(m["content"]) for m in reviewer_messages(inp, plan, draft_for_review(plan, draft), notes)),
        )
        if whole <= budget:
            return None
        batches: list[set[str]] = []
        current: set[str] = set()
        for sid in all_ids:
            if current and cost(current | {sid}) > budget:
                batches.append(current)
                current = set()
            current.add(sid)
        if current:
            batches.append(current)
        if len(batches) <= 1:
            return None
        await self.log(
            f"審查內容太長（估計輸入約 {whole} tokens，上下文 {limit}），分成 {len(batches)} 批審查",
            "warn",
            estimated_tokens=whole,
            context_length=limit,
            batches=len(batches),
        )
        return batches

    async def _review_batches(
        self,
        step: str,
        inp: RunInput,
        plan: Plan,
        draft: Draft,
        checks: list[Issue],
        batches: list[set[str]],
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """每一批各審查一次，合併 issues、review_points（去掉重複的）。回傳（合併的結果, 沒審查到的章節名稱）。"""
        merged: dict[str, Any] = {"issues": [], "review_points": [], "summary": ""}
        summaries: list[str] = []
        failed: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        for n, sections in enumerate(batches, start=1):
            batch_notes = [f"[{i.section_id}] {i.problem}" for i in checks if i.severity != "low" and i.section_id in sections]
            data = await self.chat_json(
                step,
                reviewer_messages(
                    inp, plan, draft_for_review(plan, draft, sections), batch_notes, batch=(n, len(batches))
                ),
                review_schema(),
                temperature=0.1,
                max_tokens=REVIEW_MAX_TOKENS,
            )
            if data is None:
                failed += [s.heading for s in draft.sections if s.id in sections]
                continue
            for key in ("issues", "review_points"):
                for raw in data.get(key) or []:
                    if not isinstance(raw, dict):
                        continue
                    sid = normalize_sid(raw.get("section_id"), "S", len(plan.sections)) or ""
                    if sid and sid not in sections:
                        continue  # 這一批沒有這一節，模型猜的
                    text = str(raw.get("problem") or raw.get("item") or "").strip()
                    if (key, sid, text) in seen:
                        continue
                    seen.add((key, sid, text))
                    merged[key].append(raw)
            if str(data.get("summary", "")).strip():
                summaries.append(str(data["summary"]).strip())
        if len(failed) == sum(len(b) for b in batches):
            return None, []
        merged["summary"] = f"文件太長，分成 {len(batches)} 批審查。" + " ".join(summaries)
        return merged, failed


def _section_order(draft: Draft):
    order = {s.id: i for i, s in enumerate(draft.sections)}
    return lambda sid: order.get(sid, len(order))


_SID_PREFIX_RE = re.compile(r"^\s*\[S\d+\]\s*")


def drop_check_duplicates(issues: list[Issue], checks: list[Issue]) -> list[Issue]:
    """模型又寫一次程式檢查已經找到的錯誤，不重複算。

    程式檢查的結果放在提示詞裡給模型看（寫明「已經列入，不用重複」），真模型還是常照抄成自己的錯誤，
    例如「[S5] 引用了文件裡沒有定義的編號：AC-06」（2026-11-05 審查一致性實驗）。
    同一個錯誤算兩次，錯誤數變多，「錯誤沒變少就停止修訂」也會判斷錯。

    文字包含程式檢查的說明就當成重複，不管寫在哪一節：模型照抄時常把章節寫錯（程式檢查在 S8，模型寫 S5）。
    文字不一樣時，同一節、格式錯誤、提到的編號跟程式檢查一樣，也當成重複。
    提到同一個編號的「前後矛盾」是另一個問題，保留。
    """
    found = [c for c in checks if c.severity != "low"]
    if not found:
        return issues
    kept = []
    for issue in issues:
        text = _SID_PREFIX_RE.sub("", issue.problem)
        ids = {m.group(0) for m in _ID_RE.finditer(text)}
        duplicate = False
        for check in found:
            check_ids = {m.group(0) for m in _ID_RE.finditer(check.problem)}
            if check.problem in text or text in check.problem:
                duplicate = True
            elif (
                check.section_id == issue.section_id
                and issue.kind in ("format", "")
                and check_ids
                and check_ids <= ids
            ):
                duplicate = True
            if duplicate:
                break
        if not duplicate:
            kept.append(issue)
    return kept


def resolved_numbers(data: dict[str, Any], count: int) -> set[int]:
    """模型標成已修正的序號（1 起算），只留 1～count 之間的。"""
    numbers: set[int] = set()
    for value in data.get("resolved") or []:
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= count:
            numbers.add(n)
    return numbers


def _same_issue(a: Issue, b: Issue) -> bool:
    return a.section_id == b.section_id and (
        a.problem == b.problem or bool(_quoted(a.problem) & _quoted(b.problem))
    )


def _apply_ledger(
    ledger: list[Issue], resolved: set[int], new_issues: list[Issue]
) -> tuple[list[Issue], list[str], list[Issue]]:
    """上一輪的意見：沒修正的照原樣留著（文字不變，才看得出是同一個問題）。

    回傳 (沒修正的舊問題, 已修正的舊問題描述, 真正新的問題)。
    模型標了已修正、卻又在 issues 寫一次的，當成沒修正。
    """
    repeated = {n for n, old in enumerate(ledger, start=1) if any(_same_issue(i, old) for i in new_issues)}
    still_open = [old for n, old in enumerate(ledger, start=1) if n not in resolved or n in repeated]
    fixed = [old.problem for n, old in enumerate(ledger, start=1) if n in resolved and n not in repeated]
    fresh = [i for i in new_issues if not any(_same_issue(i, old) for old in ledger)]
    return still_open, fixed, fresh
