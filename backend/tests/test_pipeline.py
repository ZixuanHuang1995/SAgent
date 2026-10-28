import asyncio

from sagent.agents.base import ListEmitter
from sagent.agents.doc_templates import load_templates
from sagent.agents.fake_llm import FakeLLM
from sagent.agents.pipeline import SpecPipeline
from sagent.agents.planner import Planner
from sagent.agents.reviewer import marked_points, static_checks, table_problems, undefined_ids
from sagent.agents.textutil import (
    clean_section_body,
    diagrams_to_markers,
    extract_item_ids,
    markers_to_diagrams,
    parse_json_object,
)
from sagent.agents.types import Draft, PipelineOptions, Plan, RunInput, SectionDraft, SectionPlan
from sagent.services.diagrams import extract_diagrams, lint_mermaid
from sagent.services.markdown_doc import parse_markdown


def make_input(mode="draft", existing="", template="functional_spec", **opts) -> RunInput:
    return RunInput(
        mode=mode,
        project_name="訂單系統",
        project_description="線上訂單管理",
        phase_name="需求分析",
        spec_title="下單功能",
        template=load_templates()[template],
        requirement="客戶可以在網站下單，訂單送出後要通知倉庫出貨。",
        existing_content=existing,
        options=PipelineOptions(**opts),
    )


def run(llm, inp):
    emitter = ListEmitter()
    result = asyncio.run(SpecPipeline(llm, emitter).run(inp))
    return result, emitter


def test_draft_pipeline_full_flow():
    llm = FakeLLM()
    result, emitter = run(llm, make_input())
    doc = parse_markdown(result.markdown)
    assert doc.title == "下單功能"
    headings = [s.heading for s in doc.sections]
    assert headings[0] == "1. 目的與範圍"
    assert headings[-1].endswith("待確認事項")
    # 圖：最多 4 張，且都通過語法檢查
    blocks = extract_diagrams(result.markdown)
    assert 1 <= len(blocks) <= 4
    assert all(lint_mermaid(b.code) == [] for b in blocks)
    assert "**圖 3-1：" in result.markdown

    # 第一次審查要求修訂 → 修訂 → 第二次審查通過
    assert [r.needs_revision for r in result.reviews] == [True, False]
    assert "補充：發生例外時" in doc.sections[1].body

    phases = [e["data"]["phase"] for e in emitter.events if e["type"] == "phase"]
    assert phases == ["plan", "write", "review", "revise", "review"]
    starts = [e["data"]["step"] for e in emitter.events if e["type"] == "step_start"]
    ends = [e["data"]["step"] for e in emitter.events if e["type"] == "step_end"]
    assert starts == ends
    assert starts[0] == "plan"
    assert all(e["data"]["ok"] for e in emitter.events if e["type"] == "step_end")
    assert any(e["type"] == "section" for e in emitter.events)
    assert emitter.tokens  # 有串流
    assert result.stats["planner"].calls == 1
    assert result.stats["reviewer"].calls == 2


def test_text_written_before_diagrams_for_cache_reuse():
    llm = FakeLLM()
    run(llm, make_input())
    purposes = [c["purpose"] for c in llm.calls]
    first_diagram = next(i for i, p in enumerate(purposes) if ":diagram:" in p)
    last_section = max(i for i, p in enumerate(purposes) if ":section:" in p)
    assert last_section < first_diagram


def test_no_diagrams_and_no_revision():
    llm = FakeLLM()
    result, _ = run(llm, make_input(diagrams=False, max_revisions=0))
    assert extract_diagrams(result.markdown) == []
    assert len(result.reviews) == 1
    assert not any(":diagram:" in c["purpose"] for c in llm.calls)


def test_fallback_when_model_cannot_output_json():
    llm = FakeLLM(fail_json=True)
    result, emitter = run(llm, make_input(max_revisions=0))
    assert result.plan.fallback is True
    assert len(result.plan.sections) == 10  # 範本的章節
    assert result.reviews[0].llm_ok is False
    warnings = [e["message"] for e in emitter.events if e["type"] == "log"]
    assert any("改用範本章節" in w for w in warnings)


EXISTING = """# 下單功能

> 文件類型：功能規格書

## 1. 目的與範圍

讓客戶線上下單。

## 2. 業務流程

1. 選商品
2. 結帳

```mermaid
flowchart TD
    A["選商品"] --> B["結帳"]
```

## 3. 待確認事項

- [x] 付款方式（已確認：信用卡）
- [ ] 是否支援退貨
"""


def test_revise_pipeline_keeps_untouched_sections():
    llm = FakeLLM()
    result, emitter = run(llm, make_input(mode="revise", existing=EXISTING, max_revisions=0))
    doc = parse_markdown(result.markdown)
    assert doc.preamble.startswith("# 下單功能")
    headings = [s.heading for s in doc.sections]
    assert headings == ["1. 目的與範圍", "2. 業務流程", "3. 修訂補充", "4. 待確認事項"]
    # E1 被修改，E2 保留原樣（含圖）
    assert "修訂：加入修訂需求提到的內容" in doc.sections[0].body
    assert 'A["選商品"] --> B["結帳"]' in doc.sections[1].body
    # 原本的待確認事項保留勾選狀態，並加上新問題
    questions = doc.sections[-1].body
    assert "- [x] 付款方式（已確認：信用卡）" in questions
    assert "- [ ] 新需求的上線時間" in questions
    plan_actions = [s.action for s in result.plan.sections]
    assert plan_actions == ["update", "keep", "write"]
    # 保留的章節不呼叫模型
    assert not any(c["purpose"] == "writer:section:S2" for c in llm.calls)


def test_revise_without_sections_falls_back_to_draft():
    llm = FakeLLM()
    result, emitter = run(llm, make_input(mode="revise", existing="只有一段文字，沒有章節", max_revisions=0))
    assert result.plan.sections[0].action == "write"
    assert any("改用撰寫模式" in e["message"] for e in emitter.events if e["type"] == "log")


def test_planner_normalizes_model_output():
    inp = make_input(max_diagrams=2)
    data = {
        "summary": "摘要",
        "sections": [
            {
                "heading": "1. 目的",
                "goal": "g",
                "key_points": ["a", "a", ""],
                "diagrams": [{"kind": "flowchart", "title": "流程", "description": ""}],
            },
            {"heading": "目的", "goal": "重複的章節", "key_points": [], "diagrams": []},
            {
                "heading": "流程",
                "goal": "g",
                "key_points": [],
                "diagrams": [
                    {"kind": "nope", "title": "x"},
                    {"kind": "sequence", "title": "互動", "description": ""},
                    {"kind": "er", "title": "ER", "description": ""},
                ],
            },
            {"heading": "待確認事項", "goal": "", "key_points": [], "diagrams": []},
        ],
        "open_questions": ["Q1", "Q1"],
    }
    plan = Planner.build_plan(inp, data)
    assert [s.heading for s in plan.sections] == ["目的", "流程"]
    assert [s.id for s in plan.sections] == ["S1", "S2"]
    assert plan.sections[0].key_points == ["a"]
    assert sum(len(s.diagrams) for s in plan.sections) == 2
    assert plan.open_questions == ["Q1"]
    assert Planner.build_plan(inp, {"sections": [{"heading": "只有一節"}]}) is None


def test_textutil_helpers():
    assert parse_json_object('好的\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('前言 {"a": {"b": 2}} 後記') == {"a": {"b": 2}}
    body = clean_section_body(
        "## 3. 業務流程\n\n說明\n\n## 子標題\n\n```mermaid\nflowchart TD\nA-->B\n```",
        "業務流程",
        keep_diagrams=False,
    )
    assert body == "說明\n\n### 子標題"
    assert clean_section_body("```markdown\n內容\n```", "x", keep_diagrams=False) == "內容"
    leaked = "部署如下圖所示（註：圖表由其他步驟產生，此處僅描述內容）。\n\n![部署圖](placeholder.png)\n\n說明（註：待確認）"
    assert clean_section_body(leaked, "x", keep_diagrams=False) == "部署如下圖所示。\n\n說明（註：待確認）"
    marked, originals = diagrams_to_markers("前\n```mermaid\nflowchart TD\nA-->B\n```\n後")
    assert marked == "前\n[[圖1]]\n後" and len(originals) == 1
    assert markers_to_diagrams("改過的前\n[[圖1]]\n後", originals).count("flowchart TD") == 1
    assert "flowchart TD" in markers_to_diagrams("模型把標記弄丟了", originals)
    assert extract_item_ids("| FR-01 | 使用者登入 |\n- BR-02：密碼錯三次鎖定") == [
        "FR-01 使用者登入",
        "BR-02 密碼錯三次鎖定",
    ]


def test_second_review_only_sends_revised_sections():
    llm = FakeLLM()
    result, _ = run(llm, make_input())
    reviews = [c for c in llm.calls if c["purpose"].startswith("reviewer:")]
    first, second = reviews[0]["messages"][-1]["content"], reviews[1]["messages"][-1]["content"]
    assert "## 文件草稿" in first and "[S1]" in first.split("## 文件草稿")[1]
    assert "## 修改過的章節" in second
    revised_part = second.split("## 修改過的章節")[1]
    assert "[S2] ## 2." in revised_part and "[S1] ## 1." not in revised_part
    # 第二輪通過；第一輪的問題已經修掉，不會被沿用
    assert result.reviews[1].issues == []
    # 第一輪模型列的人工覆核（S1）沒有修改，第二輪沿用；修訂加上的（假設）由程式列出
    points = result.reviews[1].review_points
    assert any(p.source == "llm" and p.section_id == "S1" for p in points)
    assert any(p.source == "check" and p.section_id == "S2" and "假設" in p.reason for p in points)


def test_subheadings_start_at_level_three():
    body = clean_section_body(
        "#### 開發目標\n內容\n##### 細項\n```\n#### 不是標題\n```", "目的", keep_diagrams=False
    )
    assert body.splitlines()[0] == "### 開發目標"
    assert "#### 細項" in body and "#### 不是標題" in body
    assert (
        clean_section_body("### 已經正確\n#### 子項", "x", keep_diagrams=False) == "### 已經正確\n#### 子項"
    )


def test_unwrap_only_whole_document_fences():
    # 開頭和結尾剛好都是範例程式碼：不能拆
    body = '```json\n{"a": 1}\n```\n\n說明文字\n\n```\n回應範例\n```'
    assert clean_section_body(body, "x", keep_diagrams=False) == body
    plain = "```\n說明\n```\n\n文字\n\n```\n結尾\n```"
    assert clean_section_body(plain, "x", keep_diagrams=False) == plain
    # 整段被 ``` 包住（沒寫語言、裡面沒有其他 ```）：拆開
    assert (
        clean_section_body("```\n- 項目一\n- 項目二\n```", "x", keep_diagrams=False) == "- 項目一\n- 項目二"
    )


def test_inline_marker_becomes_own_block():
    marked, originals = diagrams_to_markers("說明\n```mermaid\nflowchart TD\nA-->B\n```")
    restored = markers_to_diagrams("如下圖 [[圖1]]。後面的文字", originals)
    assert restored == "如下圖\n\n```mermaid\nflowchart TD\nA-->B\n```\n\n。後面的文字"
    assert len(extract_diagrams(restored)) == 1


def test_revise_keeps_open_questions_table():
    existing = EXISTING.replace(
        "- [x] 付款方式（已確認：信用卡）\n- [ ] 是否支援退貨\n",
        "| 編號 | 問題 |\n|---|---|\n| Q1 | 付款方式 |\n| Q2 | 退貨規則 |\n\n補充說明：\n  第二行縮排\n",
    )
    result, _ = run(FakeLLM(), make_input(mode="revise", existing=existing, max_revisions=0))
    questions = parse_markdown(result.markdown).sections[-1].body
    assert "| Q2 | 退貨規則 |" in questions
    assert "  第二行縮排" in questions
    assert questions.rstrip().endswith("- [ ] 新需求的上線時間")


def test_revise_without_preamble_adds_nothing():
    existing = "## 1. 目的\n\n內容\n\n## 2. 流程\n\n步驟"
    result, _ = run(FakeLLM(), make_input(mode="revise", existing=existing, max_revisions=0))
    assert result.markdown.startswith("## 1. 目的")


def test_uiux_advice_template():
    tpl = load_templates()["uiux_advice"]
    assert tpl.name == "UI/UX 設計建議"
    assert tpl.advice and not tpl.allow_diagrams
    assert tpl.role == "UI/UX 設計師"
    assert all(not s.diagrams for s in tpl.sections)
    assert tpl.to_dict()["advice"] is True and tpl.to_dict()["allow_diagrams"] is False
    # 其他範本維持原本的設定
    spec = load_templates()["functional_spec"]
    assert not spec.advice and spec.allow_diagrams and spec.role == "系統分析師"


def test_advice_doc_uses_advice_rules_and_draws_nothing():
    llm = FakeLLM()
    inp = make_input(template="uiux_advice", diagrams=True, max_diagrams=4)
    result, _ = run(llm, inp)
    assert inp.options.diagrams is False
    assert extract_diagrams(result.markdown) == []
    assert not any(":diagram:" in c["purpose"] for c in llm.calls)

    planner_system = llm.calls[0]["messages"][0]["content"]
    assert planner_system.startswith("你是資深 UI/UX 設計師")
    assert "這次不畫圖" in planner_system
    writer = [c["messages"][0]["content"] for c in llm.calls if ":section:" in c["purpose"]]
    assert writer and all("這是建議文件" in s and "只根據需求內容撰寫" not in s for s in writer)
    reviewer = [c["messages"][0]["content"] for c in llm.calls if c["purpose"].startswith("reviewer")]
    assert reviewer and all("這是建議文件" in s and "提出需求沒寫的建議是正常的" in s for s in reviewer)
    # 目標寫了 UX 編號的章節才編號
    assert "| UX-02 |" in result.markdown
    assert "FR-" not in result.markdown


def test_spec_doc_keeps_spec_rules():
    llm = FakeLLM()
    run(llm, make_input(max_revisions=0))
    writer = [c["messages"][0]["content"] for c in llm.calls if ":section:" in c["purpose"]]
    assert all(s.startswith("你是系統分析師") and "只根據需求內容撰寫" in s for s in writer)
    assert not any("這是建議文件" in s for s in writer)


def test_template_categories_and_styles(tmp_path):
    templates = load_templates()
    assert templates["idea_proposal"].category == "idea" and templates["idea_proposal"].idea
    assert templates["change_request"].category == "change"
    assert templates["api_spec"].category == "design"
    assert templates["functional_spec"].category == "feature" and templates["functional_spec"].style == "spec"
    # 預設範本還是功能規格書
    assert next(iter(templates)) == "functional_spec"
    # 自訂範本：舊寫法 advice: true 仍然有效；沒寫或寫錯類型歸到「功能」
    (tmp_path / "old.yaml").write_text(
        "key: old\nname: 舊範本\nadvice: true\ncategory: nope\nsections:\n  - heading: 一\n", encoding="utf-8"
    )
    old = load_templates(tmp_path)["old"]
    assert old.advice and old.style == "advice" and old.category == "feature"
    assert old.max_diagrams is None


def test_idea_doc_uses_idea_rules_and_one_diagram():
    llm = FakeLLM()
    inp = make_input(template="idea_proposal", diagrams=True, max_diagrams=4)
    result, _ = run(llm, inp)
    assert inp.options.max_diagrams == 1
    assert len(extract_diagrams(result.markdown)) <= 1

    planner_system = llm.calls[0]["messages"][0]["content"]
    assert "「構想提案」文件" in planner_system and "這是構想文件" in planner_system
    writer = [c["messages"][0]["content"] for c in llm.calls if ":section:" in c["purpose"]]
    assert writer and all("這是構想文件" in s and "只根據需求內容撰寫" not in s for s in writer)
    reviewer = [c["messages"][0]["content"] for c in llm.calls if c["purpose"].startswith("reviewer")]
    assert reviewer and all("這是構想文件" in s and "不要要求補上細節" in s for s in reviewer)
    # 可能的做法這一節的目標允許提出建議
    user = next(c["messages"][1]["content"] for c in llm.calls if "第 4 節：可能的做法" in c["messages"][1]["content"])
    assert "可以提出需求沒寫的做法" in user
    assert "FR-" not in result.markdown


def test_algorithm_doc_uses_algorithm_rules_and_keeps_pseudocode():
    templates = load_templates()
    assert templates["algorithm_design"].category == "algorithm" and templates["algorithm_design"].algorithm
    llm = FakeLLM()
    inp = make_input(template="algorithm_design", diagrams=True, max_diagrams=4)
    result, _ = run(llm, inp)
    assert inp.options.max_diagrams == 2

    planner_system = llm.calls[0]["messages"][0]["content"]
    assert "「演算法設計」文件" in planner_system and "這是演算法設計文件" in planner_system
    writer = [c["messages"][0]["content"] for c in llm.calls if ":section:" in c["purpose"]]
    assert writer and all(s.startswith("你是演算法工程師") and "解題方法、資料結構和虛擬碼由你設計" in s for s in writer)
    assert not any("只根據需求內容撰寫" in s or "這是建議文件" in s for s in writer)
    reviewer = [c["messages"][0]["content"] for c in llm.calls if c["purpose"].startswith("reviewer")]
    assert reviewer and all("這是演算法設計文件" in s and "不算編造" in s for s in reviewer)
    # 虛擬碼是 ```text 區塊，不是圖，Writer 整理內文時要保留
    assert "```text\nfunction solve(input)" in result.markdown
    assert all(b.lang == "mermaid" for b in extract_diagrams(result.markdown))


def test_experiment_doc_uses_experiment_rules():
    templates = load_templates()
    assert templates["experiment_design"].category == "algorithm" and templates["experiment_design"].experiment
    llm = FakeLLM()
    inp = make_input(template="experiment_design", diagrams=True, max_diagrams=4)
    result, _ = run(llm, inp)
    assert inp.options.max_diagrams == 2

    planner_system = llm.calls[0]["messages"][0]["content"]
    assert "「實驗設計」文件" in planner_system and "這是實驗設計文件" in planner_system
    writer = [c["messages"][0]["content"] for c in llm.calls if ":section:" in c["purpose"]]
    assert writer and all(s.startswith("你是實驗設計研究員") and "不要寫出實驗結果" in s for s in writer)
    assert not any("只根據需求內容撰寫" in s or "這是演算法設計文件" in s for s in writer)
    reviewer = [c["messages"][0]["content"] for c in llm.calls if c["purpose"].startswith("reviewer")]
    assert reviewer and all("這是實驗設計文件" in s and "不算編造" in s for s in reviewer)
    # 研究假設、指標照章節目標編號
    assert "H-02" in result.markdown and "M-03" in result.markdown


def _draft(*bodies: str) -> tuple[Plan, Draft]:
    secs = [SectionDraft(id=f"S{i}", heading=f"第{i}節", body=b) for i, b in enumerate(bodies, start=1)]
    plan = Plan(title="t", summary="", sections=[SectionPlan(id=s.id, heading=s.heading, goal="") for s in secs])
    return plan, Draft(sections=secs)


def test_reviewer_checks_table_columns():
    body = "\n".join(
        [
            "| 編號 | 說明 |",
            "|---|---|",
            "| FR-01 | 登入 |",
            "| FR-02 | 登出 | 多一欄 |",
            "",
            "`a|b` 不算表格",
        ]
    )
    assert table_problems(body) == ["第 1 個表格有 1 列的欄位數跟標題列（2 欄）不一樣"]
    # 對齊記號、跳脫的 \| 都不算錯
    assert table_problems("| a | b |\n|:--|--:|\n| 1 | 2 |\n| 3 \\| 4 | 5 |") == []


def test_reviewer_checks_undefined_ids():
    plan, draft = _draft(
        "| 編號 | 說明 |\n|---|---|\n| FR-01 | 登入 |\n\n- **BR-01**：密碼錯三次鎖定",
        "- AC-01：對應 FR-01、FR-07，參考 ISO-27001、UTF-8\n\n```mermaid\nflowchart LR\n  A[FR-09] --> B\n```",
    )
    # FR-07 沒有定義；圖裡的 FR-09 不算；AC-01 在條列開頭算定義
    assert undefined_ids(draft) == {"S2": ["FR-07"]}
    issues = static_checks(plan, draft)
    assert [(i.section_id, i.severity) for i in issues] == [("S2", "medium")]
    assert "FR-07" in issues[0].problem


def test_reviewer_lists_marked_points():
    plan, draft = _draft(
        "- 緊急報修 30 分鐘內派工（假設）。\n- 一般報修當天派工。",
        "| 項目 | 說明 |\n|---|---|\n| 保存期限 | 5 年（待確認） |\n\n- 緊急報修 30 分鐘內派工（假設）。",
    )
    points = marked_points(draft)
    assert [(p.section_id, p.item, p.source) for p in points] == [
        ("S1", "緊急報修 30 分鐘內派工（假設）。", "check"),
        ("S2", "保存期限 / 5 年（待確認）", "check"),
    ]
    assert "假設" in points[0].reason and points[1].reason == "文件標註為待確認"
