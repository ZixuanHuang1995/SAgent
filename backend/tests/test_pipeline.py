import asyncio

from sagent.agents.base import ListEmitter
from sagent.agents.doc_templates import load_templates
from sagent.agents.fake_llm import FakeLLM
from sagent.agents.pipeline import SpecPipeline
from sagent.agents.planner import Planner
from sagent.agents.textutil import (
    clean_section_body,
    diagrams_to_markers,
    extract_item_ids,
    markers_to_diagrams,
    parse_json_object,
)
from sagent.agents.types import PipelineOptions, RunInput
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
    assert reviewer and all("需求沒寫的建議是正常的" in s for s in reviewer)
    # 目標寫了 UX 編號的章節才編號
    assert "| UX-02 |" in result.markdown
    assert "FR-" not in result.markdown


def test_spec_doc_keeps_spec_rules():
    llm = FakeLLM()
    run(llm, make_input(max_revisions=0))
    writer = [c["messages"][0]["content"] for c in llm.calls if ":section:" in c["purpose"]]
    assert all(s.startswith("你是系統分析師") and "只根據需求內容撰寫" in s for s in writer)
    assert not any("這是建議文件" in s for s in writer)
