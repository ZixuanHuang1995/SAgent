"""三個 agent 的提示詞。

給小模型的提示詞原則：
- 規則用編號條列，一條只講一件事
- 輸出格式講清楚，JSON 另外用 schema 約束
- 共用的內容（需求、大綱）放前面，同一個 agent 連續呼叫時 Ollama 可以重用快取，CPU 上會快很多
"""

from __future__ import annotations

import re

from .diagram_kinds import KINDS, kinds_text
from .doc_templates import DocTemplate
from .types import Issue, Plan, RunInput, SectionPlan

_COMMON_RULES = [
    "使用繁體中文（台灣用語），技術名詞可以保留英文。",
    "句子簡單直白，一句只講一件事，不要寫空泛的形容詞。",
    "多用條列和 Markdown 表格。小標題用 ###。",
]
_SPEC_RULES = [
    "只根據需求內容撰寫。需求沒提到的細節不要編造；必要的假設標註「（假設）」，不確定的地方標註「（待確認）」。",
    "編號格式：功能需求 FR-01、業務規則 BR-01、非功能需求 NFR-01、驗收條件 AC-01。只有這一節的目標或要點寫明要編號時才新增編號；其他章節提到這些項目時，引用前面已經定義的編號，不要重新編號，也不要發明新的編號種類。",
]
# 建議類文件（範本 advice: true）：可以提出需求沒寫的做法，但需求沒有的事實不能編造
_ADVICE_RULES = [
    "這是建議文件，可以提出需求沒有寫的做法。每條建議要具體，看了就知道怎麼做，並寫出理由：對應哪個需求、哪類使用者或哪個常見問題。",
    "用「建議…」的寫法，不要寫成「系統必須…」。",
    "需求沒提到的事實（數字、既有系統、使用者人數、調查結果）不要編造；必要的假設標註「（假設）」，不確定的地方標註「（待確認）」。",
    "只有這一節的目標或要點寫明要編號時才新增編號，格式照目標寫的（例如 UX-01）；其他章節提到時引用前面已經定義的編號，不要重新編號。",
]
# 構想文件（範本 style: idea）：想法還不成熟，只整理，不寫細節規格。
# 只有目標寫明「可以提出需求沒寫的做法」的章節（例如「可能的做法」）才能提建議
_IDEA_RULES = [
    "這是構想文件，想法還沒確定。整理需求裡的想法，不要寫細節規格：不寫欄位、API、畫面細節，也不要寫 FR、BR、AC 這類編號需求。",
    "需求沒提到的事實（數字、既有系統、使用者人數、調查結果）不要編造。資訊不夠的章節就寫短一點，不清楚的地方標註「（待確認）」，必要的假設標註「（假設）」。",
    "只有章節目標寫明「可以提出需求沒寫的做法」時，才能提出需求沒寫的做法，並用「建議…」的寫法、寫出理由。其他章節只整理需求裡有的內容。",
]


def _style_rules(template: DocTemplate) -> list[str]:
    if template.idea:
        return _IDEA_RULES
    return _ADVICE_RULES if template.advice else _SPEC_RULES


def _rules(template: DocTemplate, *extra: str) -> str:
    rules = [*_COMMON_RULES, *_style_rules(template), *extra]
    return "寫作規則：\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(rules, start=1))


def _doc_noun(template: DocTemplate) -> str:
    return f"「{template.name}」文件" if template.advice or template.idea else "系統分析文件"


def _with_role(prefix: str, template: DocTemplate) -> str:
    """「資深」+「系統分析師」不用空格，「資深」+「UI/UX 設計師」中間加空格。"""
    role = template.role
    return f"{prefix} {role}" if role[:1].isascii() else f"{prefix}{role}"


def _doc_info(inp: RunInput) -> str:
    lines = [f"專案：{inp.project_name}"]
    if inp.project_description.strip():
        lines.append(f"專案說明：{inp.project_description.strip()}")
    lines += [
        f"階段：{inp.phase_name}",
        f"文件標題：{inp.spec_title}",
        f"文件類型：{inp.template.name}（{inp.template.description}）",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

_DIAGRAM_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "title": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["kind", "title", "description"],
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "goal": {"type": "string"},
                    "key_points": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "diagrams": {"type": "array", "items": _DIAGRAM_ITEM_SCHEMA, "maxItems": 2},
                },
                "required": ["heading", "goal", "key_points", "diagrams"],
            },
            "maxItems": 12,
        },
        "open_questions": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
    },
    "required": ["title", "summary", "sections", "open_questions"],
}

REVISE_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["update", "remove"]},
                    "instruction": {"type": "string"},
                },
                "required": ["section_id", "action", "instruction"],
            },
        },
        "new_sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "after": {"type": "string"},
                    "heading": {"type": "string"},
                    "goal": {"type": "string"},
                    "key_points": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "diagrams": {"type": "array", "items": _DIAGRAM_ITEM_SCHEMA, "maxItems": 2},
                },
                "required": ["after", "heading", "goal", "key_points", "diagrams"],
            },
            "maxItems": 5,
        },
        "open_questions": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
    },
    "required": ["summary", "changes", "new_sections", "open_questions"],
}


def planner_messages(inp: RunInput) -> list[dict[str, str]]:
    opts = inp.options
    diagram_rule = (
        f"4. 只有真的需要圖的章節才放 diagrams，整份文件最多 {opts.max_diagrams} 張圖。kind 只能用：{kinds_text()}。"
        if opts.diagrams and opts.max_diagrams > 0
        else "4. 這次不畫圖，每個章節的 diagrams 都給空陣列。"
    )
    tpl = inp.template
    advice_rule = ""
    if tpl.advice:
        advice_rule = "\n8. 這是建議文件：key_points 寫這一節要給的具體建議方向，要跟需求裡的使用者、工作或資料有關。"
    elif tpl.idea:
        advice_rule = (
            "\n8. 這是構想文件：章節少一點、內容短一點，不要規劃欄位、API、畫面細節這類細節規格的章節。"
            "想法還不清楚的地方，多寫進 open_questions。"
        )
    system = f"""你是{_with_role("資深", tpl)}，現在要「規劃」一份{_doc_noun(tpl)}的大綱。你只負責規劃，不用寫內文。

規劃規則：
1. 參考「建議章節」，依照需求內容調整：跟需求無關的章節可以刪除，缺少的可以新增。章節數量 4 到 {opts.max_sections} 個。
2. heading 只寫章節名稱，不要加編號。不要規劃「待確認事項」章節，系統會自動加。
3. 每個章節寫 goal（這一節要寫什麼，一句話）和 key_points（2 到 4 個具體要點，每個要點一句短句，盡量使用需求裡的名詞）。
{diagram_rule}
5. 需求中不清楚、缺少或互相矛盾的地方，寫進 open_questions，不要自己猜。
6. summary 用一兩句話說明這份文件的內容。
7. 使用繁體中文。只輸出 JSON，不要縮排和多餘的空白。{advice_rule}"""

    suggested = []
    for sec in inp.template.sections:
        diagrams = "、".join(f"{d} {KINDS[d].name}" for d in sec.diagrams) if sec.diagrams else "無"
        suggested.append(f"- {sec.heading}：{sec.goal}（建議的圖：{diagrams}）")
    user = f"""## 文件資訊
{_doc_info(inp)}

## 建議章節
{chr(10).join(suggested)}

## 需求內容
{inp.requirement.strip()}

請輸出大綱 JSON。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _section_digest(body: str, limit: int = 200) -> str:
    text = re.sub(r"```.*?```", "[圖]", body, flags=re.S)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def revise_planner_messages(inp: RunInput, existing: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    """existing：[(E1, 標題, 內文), …]"""
    opts = inp.options
    diagram_rule = (
        f"新增章節需要圖時才放 diagrams，kind 只能用：{kinds_text()}。"
        if opts.diagrams and opts.max_diagrams > 0
        else "這次不畫圖，diagrams 都給空陣列。"
    )
    system = f"""你是{_with_role("資深", inp.template)}，現在要「規劃如何修訂」一份既有的{_doc_noun(inp.template)}。你只負責規劃，不用寫內文。

規劃規則：
1. 看「修訂需求」，找出哪些既有章節要修改、哪些要刪除、需不需要新增章節。
2. 不用改的章節不要列出來，系統會原樣保留。
3. changes 每一項寫 section_id（例如 E2）、action（update 修改或 remove 刪除）和 instruction（具體說明要怎麼改）。
4. 要新增的章節放在 new_sections：after 寫要放在哪個 section_id 後面（放最後寫 END），heading 不要加編號。{diagram_rule}
5. 修訂需求中不清楚的地方寫進 open_questions。
6. summary 用一句話說明這次修訂的重點。
7. 使用繁體中文。只輸出 JSON，不要縮排和多餘的空白。"""
    listing = "\n".join(
        f"[{sid}] {heading}\n摘要：{_section_digest(body)}" for sid, heading, body in existing
    )
    user = f"""## 文件資訊
{_doc_info(inp)}

## 既有章節
{listing}

## 修訂需求
{inp.requirement.strip()}

請輸出修訂計畫 JSON。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def writer_system(template: DocTemplate) -> str:
    rules = _rules(
        template,
        "只寫指定的這一節。不要寫章節標題，不要寫其他章節的內容，不要寫開場白或結語。",
        "不要畫圖，不要輸出 ```mermaid 或 ```plantuml，圖會由另一個步驟產生。",
    )
    return f"""{_with_role("你是", template)}，負責撰寫{_doc_noun(template)}中的「一個章節」。

{rules}

直接輸出這一節的 Markdown 內文。"""


def _outline(plan: Plan) -> str:
    return "\n".join(f"{i}. {s.heading}" for i, s in enumerate(plan.sections, start=1))


def _writer_context(inp: RunInput, plan: Plan) -> str:
    """同一次執行中每次寫章節都一樣的前段，讓 Ollama 重用快取。"""
    return f"""## 文件資訊
{_doc_info(inp)}
文件摘要：{plan.summary}

## 需求內容
{inp.requirement.strip()}

## 文件大綱
{_outline(plan)}"""


def section_messages(
    inp: RunInput, plan: Plan, sec: SectionPlan, number: int, known_items: list[str]
) -> list[dict[str, str]]:
    points = "\n".join(f"- {p}" for p in sec.key_points) or "- （依目標撰寫）"
    extra = ""
    if sec.diagrams:
        titles = "、".join(d.title for d in sec.diagrams)
        extra += f"\n這一節後面會附上圖：{titles}。內文可以寫「如下圖」，但不要自己畫。"
    if known_items:
        extra += "\n\n前面章節已經定義的編號項目（引用時請用同樣的編號）：\n" + "\n".join(
            f"- {item}" for item in known_items
        )
    user = f"""{_writer_context(inp, plan)}

## 現在要寫的章節
第 {number} 節：{sec.heading}
目標：{sec.goal}
要點：
{points}{extra}

請輸出第 {number} 節「{sec.heading}」的內文。"""
    return [{"role": "system", "content": writer_system(inp.template)}, {"role": "user", "content": user}]


def update_system(template: DocTemplate) -> str:
    rules = _rules(
        template,
        "只輸出這一節修改後的完整內文。不要寫章節標題，不要寫其他章節，不要說明你改了什麼。",
        "沒有要改的部分照原樣保留。",
        "原本的圖（```mermaid 或 ```plantuml 區塊）要保留；修訂說明要求改圖時才修改圖的內容，並維持原本的語法。",
    )
    return f"""{_with_role("你是", template)}，負責依照修訂說明修改{_doc_noun(template)}中的「一個章節」。

{rules}"""


def update_section_messages(inp: RunInput, plan: Plan, sec: SectionPlan) -> list[dict[str, str]]:
    user = f"""## 文件資訊
{_doc_info(inp)}

## 修訂需求
{inp.requirement.strip()}

## 這次修訂的重點
{plan.summary}

## 要修改的章節：{sec.heading}
（原本內容開始）
{sec.source_body.strip()}
（原本內容結束）

## 修改說明
{sec.instruction}

請輸出修改後「{sec.heading}」這一節的完整內文。"""
    return [{"role": "system", "content": update_system(inp.template)}, {"role": "user", "content": user}]


def revise_system(template: DocTemplate) -> str:
    rules = _rules(
        template,
        "只輸出這一節修改後的完整內文。不要寫章節標題，不要寫其他章節，不要說明你改了什麼。",
        "內文中的 [[圖1]]、[[圖2]] 是圖的位置標記，一定要原樣保留在原本的位置。",
        "不要輸出 ```mermaid 或 ```plantuml。",
    )
    return f"""{_with_role("你是", template)}，負責依照審查意見修改{_doc_noun(template)}中的「一個章節」。

{rules}"""


def revise_section_messages(
    inp: RunInput, plan: Plan, sec: SectionPlan, body_with_markers: str, issues: list[Issue]
) -> list[dict[str, str]]:
    comments = "\n".join(
        f"- （{i.severity}）問題：{i.problem}" + (f"；建議：{i.suggestion}" if i.suggestion else "")
        for i in issues
    )
    user = f"""{_writer_context(inp, plan)}

## 要修改的章節：{sec.heading}
目標：{sec.goal}
（目前內容開始）
{body_with_markers.strip()}
（目前內容結束）

## 審查意見
{comments}

請依照審查意見修改「{sec.heading}」，輸出修改後的完整內文。"""
    return [{"role": "system", "content": revise_system(inp.template)}, {"role": "user", "content": user}]


DIAGRAM_SYSTEM = """你負責畫系統分析文件裡的圖。

規則：
1. 只輸出一個程式碼區塊（```mermaid 或 ```plantuml），區塊外面不要有任何文字。
2. 嚴格照「語法規則」和「範例」的寫法，範例怎麼寫你就怎麼寫。
3. 圖的內容要跟章節內文一致。顯示的文字用繁體中文，語法規則要求用英文的地方才用英文。
4. 圖不要太大：節點、參與者或實體最多 12 個。"""


def diagram_messages(
    inp: RunInput, heading: str, section_text: str, kind: str, title: str, description: str, syntax: str
) -> list[dict[str, str]]:
    info = KINDS[kind]
    lang = "plantuml" if syntax == "plantuml" else "mermaid"
    guide = info.plantuml_guide if lang == "plantuml" else info.mermaid_guide
    example = info.plantuml_example if lang == "plantuml" else info.mermaid_example
    text = re.sub(r"```.*?```", "", section_text, flags=re.S).strip()
    user = f"""## 文件
{inp.spec_title}

## 需求內容（節錄）
{inp.requirement.strip()[:1500]}

## 章節內文：{heading}
{text[:2500]}

## 要畫的圖
類型：{info.name}
標題：{title}
說明：{description or title}

## 語法：{"PlantUML" if lang == "plantuml" else "Mermaid"}
語法規則：
{guide}

範例：
```{lang}
{example}
```

請輸出「{title}」的 ```{lang} 程式碼區塊。"""
    return [{"role": "system", "content": DIAGRAM_SYSTEM}, {"role": "user", "content": user}]


def diagram_fix_message(errors: list[str], lang: str) -> dict[str, str]:
    errs = "\n".join(f"- {e}" for e in errors)
    return {
        "role": "user",
        "content": f"這段程式碼有語法錯誤：\n{errs}\n\n請照語法規則和範例修正，重新輸出完整的 ```{lang} 程式碼區塊，不要其他文字。",
    }


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "problem": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["section_id", "severity", "problem", "suggestion"],
            },
            "maxItems": 8,
        },
        "review_points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "item": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["section_id", "item", "reason"],
            },
            "maxItems": 10,
        },
        "summary": {"type": "string"},
    },
    "required": ["issues", "review_points", "summary"],
}

# 三種文件的審查重點一樣（格式、邏輯錯誤，以及要人工覆核的地方），只差這一行說明
_REVIEW_STYLE_NOTE = {
    "spec": "這是規格文件：內容要根據需求。需求沒提到、文件卻寫得很具體的內容（數字、期限、規則、欄位），列為人工覆核。",
    "advice": (
        "這是建議文件：提出需求沒寫的建議是正常的，不算錯誤，也不用列人工覆核。"
        "但建議裡用到需求沒提到的事實（數字、既有系統、使用者人數、調查結果），要列為人工覆核。"
    ),
    "idea": (
        "這是構想文件：想法還沒確定，內容本來就不完整，不要要求補上細節。"
        "寫成細節規格（欄位、API、FR 編號需求）算格式錯誤。「可能的做法」這類章節提出需求沒寫的做法是正常的，不算錯誤。"
    ),
}


def reviewer_system(template: DocTemplate) -> str:
    """Reviewer 只做兩件事：找出格式、邏輯錯誤（交給 Writer 修），找出要人工覆核的地方（列給使用者）。

    不評論寫得好不好、完不完整，也不打分數。
    """
    return f"""你是嚴格但務實的文件審查員。請對照「需求內容」審查「文件草稿」，只做下面兩件事。

一、找出錯誤，寫在 issues。這些會交給撰寫者修正：
1. 格式錯誤：表格欄位數不一致、編號重複或跳號、引用了沒有定義的編號、條列或標題格式混亂。
2. 邏輯錯誤：同一件事在不同章節寫得不一樣（數字、期限、狀態、角色、流程）、跟需求矛盾、條件互相衝突、流程或狀態轉換有缺口（例如進得去出不來的狀態）。

二、找出需要人工覆核的地方，寫在 review_points。這些只列給使用者確認，不會自動修改：
3. 需求沒提到、文件卻寫得很具體的內容，可能是撰寫者自己補的。
4. 需要業務單位或主管決定的事。
已經標註（假設）、（待確認）的地方程式會自動列出，不用重複。

{_REVIEW_STYLE_NOTE.get(template.style, _REVIEW_STYLE_NOTE["spec"])}

規則：
1. 不要評論寫得好不好、完不完整，不要挑剔用字和文筆，也不要要求補上需求沒寫的內容。
2. issues 最多 6 個，每個寫 section_id（例如 S2）、severity、problem（哪裡錯、為什麼錯）、suggestion（具體怎麼改）。
3. severity：high 是邏輯錯誤或跟需求矛盾；medium 是格式錯誤；low 是不影響理解的小問題。
4. review_points 最多 8 個，每個寫 section_id、item（要確認的內容，一句話）、reason（為什麼要人工確認）。
5. 沒有問題時給空陣列，不要硬找。
6. 需求內容裡的條列編號（1、2、3…）只是順序，跟文件裡的編號無關，不要拿來對照。
7. 同一件事在不同章節出現（例如功能需求和驗收條件都提到）是正常的，只有寫得不一樣才算錯誤。
8. summary 用一兩句話說明審查結果。
9. 使用繁體中文。只輸出 JSON，不要縮排和多餘的空白。"""


def reviewer_messages(
    inp: RunInput, plan: Plan, draft_text: str, check_notes: list[str], partial: bool = False
) -> list[dict[str, str]]:
    outline = "\n".join(f"[{s.id}] {s.heading}" for s in plan.sections)
    checks = "\n".join(f"- {n}" for n in check_notes) if check_notes else "- 沒有發現格式問題"
    draft_title = (
        "## 修改過的章節\n（其他章節上一輪已經審查過，這次只審查下面這些章節，section_id 只能用這些章節的編號）"
        if partial
        else "## 文件草稿"
    )
    user = f"""## 文件資訊
{_doc_info(inp)}

## 需求內容
{inp.requirement.strip()}

## 章節
{outline}

## 程式檢查找到的格式問題（已經列入，不用重複）
{checks}

{draft_title}
{draft_text}

請輸出審查結果 JSON。"""
    return [{"role": "system", "content": reviewer_system(inp.template)}, {"role": "user", "content": user}]


def json_retry_message() -> dict[str, str]:
    return {"role": "user", "content": "上一次的輸出不是正確的 JSON。請重新輸出，只輸出 JSON，不要其他文字。"}
