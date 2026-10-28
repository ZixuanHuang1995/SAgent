import json
from pathlib import Path

import pytest

from sagent.agents.diagram_kinds import KINDS, resolve_syntax
from sagent.services.diagram_snippets import SNIPPETS
from sagent.services.diagrams import (
    extract_code,
    extract_diagrams,
    fix_mermaid,
    fix_plantuml,
    lint_mermaid,
    lint_plantuml,
    normalize_diagram,
    transform_diagrams,
)
from sagent.services.plantuml import encode_plantuml


def test_all_examples_pass_lint():
    """給模型看的範例本身一定要通過檢查。"""
    for kind in KINDS.values():
        assert lint_mermaid(kind.mermaid_example) == [], kind.key
        assert lint_plantuml(kind.plantuml_example) == [], kind.key
        assert fix_mermaid(kind.mermaid_example) == kind.mermaid_example.strip(), kind.key


def test_snippets_pass_lint():
    """「插入圖表」的骨架要通過檢查，AI 修訂文件時才不會被當成錯誤。"""
    assert {k for k in KINDS} <= {s.key for s in SNIPPETS}, "AI 的每種圖都要有骨架，範本的常用圖表才對得起來"
    assert len({s.key for s in SNIPPETS}) == len(SNIPPETS)
    for s in SNIPPETS:
        assert lint_mermaid(s.mermaid) == [], s.key
        assert fix_mermaid(s.mermaid) == s.mermaid.strip(), s.key
        if s.plantuml:
            assert lint_plantuml(s.plantuml) == [], s.key


def test_snippets_parsed_by_real_mermaid():
    """骨架改了要用 frontend/scripts/mermaid-validate.mjs 重新檢查，更新 mermaid_cases.json 的 snippet_* 案例。"""
    for s in SNIPPETS:
        case = _CORPUS["cases"].get(f"snippet_{s.key}")
        assert case and case["code"] == s.mermaid, f"{s.key}：mermaid_cases.json 的案例跟骨架不一樣"
        assert case["valid"], s.key


def test_extract_and_transform():
    md = "文字\n\n```mermaid\nflowchart TD\n  A --> B\n```\n\n```python\nprint(1)\n```\n\n~~~plantuml\n@startuml\nA -> B\n@enduml\n~~~\n"
    blocks = extract_diagrams(md)
    assert [b.lang for b in blocks] == ["mermaid", "plantuml"]
    out = transform_diagrams(md, lambda i, b: b.code.upper() if b.lang == "mermaid" else None)
    assert "FLOWCHART TD" in out
    assert "~~~plantuml\n@startuml\nA -> B\n@enduml\n~~~" in out
    assert "print(1)" in out


def test_transform_unclosed_block():
    md = "```mermaid\nflowchart TD\nA --> B"
    out = transform_diagrams(md, lambda i, b: "flowchart LR\nA --> C")
    assert out == "```mermaid\nflowchart LR\nA --> C\n```"


def test_fix_quotes_labels_with_special_chars():
    code = "flowchart TD\n    A[使用者 (管理員)] --> B{是否通過?}\n    B -->|送出(表單)| C[完成]\n    C -- 回到首頁 --> A"
    fixed = fix_mermaid(code)
    assert 'A["使用者 (管理員)"]' in fixed
    assert "B{是否通過?}" in fixed
    assert '-->|"送出(表單)"|' in fixed
    assert "-->|回到首頁|" in fixed
    assert lint_mermaid(fixed) == []


def test_fix_keeps_valid_shapes():
    code = 'flowchart LR\n    DB[(資料庫)] --> S([開始])\n    A((圓)) --> B["已經 (有引號)"]\n    subgraph G[群組 (後台)]\n    X --> Y\n    end'
    fixed = fix_mermaid(code)
    assert "DB[(資料庫)]" in fixed
    assert "S([開始])" in fixed
    assert "A((圓))" in fixed
    assert 'B["已經 (有引號)"]' in fixed
    assert 'subgraph G["群組 (後台)"]' in fixed
    assert lint_mermaid(fixed) == []


def test_fix_removes_leading_text():
    assert fix_mermaid("以下是流程圖：\nflowchart TD\n  A --> B").startswith("flowchart TD")


def test_lint_mermaid_errors():
    assert lint_mermaid("") == ["圖表是空的"]
    assert "第一行" in lint_mermaid("A --> B")[0]
    assert lint_mermaid("sequenceDiagram\n  A->>B 沒有冒號")
    assert lint_mermaid("sequenceDiagram\n  loop 重試\n  A->>B: hi")  # 少 end
    assert lint_mermaid("erDiagram\n  USER {\n    string 名稱\n  }") == []  # 中文欄位名稱可以
    assert lint_mermaid("erDiagram\n  USER {\n    string 客戶 名稱\n  }")  # 名稱中間有空白不行
    assert lint_mermaid("erDiagram\n  USER ||--o{ ORDER : places") == []
    assert lint_mermaid("flowchart TD\n  A --> end")
    assert lint_mermaid("flowchart TD\n  subgraph S\n  A --> B")
    assert lint_mermaid("@startuml\nA -> B\n@enduml")


def test_sequence_keywords_case_insensitive():
    code = "sequenceDiagram\n    participant A as 前端\n    Note right of A: 說明\n    A->>B: 呼叫\n    Loop 每 5 秒\n    A->>B: 輪詢\n    end"
    assert lint_mermaid(code) == []


def test_plantuml_fix_and_lint():
    assert fix_plantuml("A -> B : hi") == "@startuml\nA -> B : hi\n@enduml"
    assert fix_plantuml("說明\n@startuml\nA -> B\n").endswith("@enduml")
    assert lint_plantuml("@startuml\n@enduml") == ["圖表沒有內容"]
    assert lint_plantuml("@startuml\nflowchart TD\nA --> B\n@enduml")
    code, errors = normalize_diagram("plantuml", "actor U\nU -> (登入)")
    assert errors == [] and code.startswith("@startuml")


def test_extract_code_from_chatty_reply():
    reply = "<think>想一下</think>好的，這是圖：\n```mermaid\nflowchart TD\nA --> B\n```\n希望有幫助"
    assert extract_code(reply, "mermaid") == "flowchart TD\nA --> B"
    assert extract_code("flowchart LR\nA --> B", "mermaid") == "flowchart LR\nA --> B"
    assert extract_code("說明\n@startuml\nA -> B\n@enduml\n其他", "plantuml") == "@startuml\nA -> B\n@enduml"


def test_resolve_syntax():
    assert resolve_syntax("usecase", "auto", plantuml_available=False) == "mermaid"
    assert resolve_syntax("usecase", "auto", plantuml_available=True) == "plantuml"
    assert resolve_syntax("sequence", "auto", plantuml_available=True) == "mermaid"
    assert resolve_syntax("sequence", "plantuml", plantuml_available=False) == "plantuml"
    assert resolve_syntax("component", "mermaid", plantuml_available=True) == "mermaid"


def test_plantuml_encoding_known_value():
    # PlantUML 官方文件的範例：「Bob -> Alice : hello」
    assert encode_plantuml("Bob -> Alice : hello") == "SyfFKj2rKt3CoKnELR1Io4ZDoSa70000"


# tests/data/mermaid_cases.json 是用前端同版本的 Mermaid 實際解析產生的標準答案
# （產生方式見 frontend/scripts/mermaid-validate.mjs）。
_CORPUS = json.loads((Path(__file__).parent / "data" / "mermaid_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", list(_CORPUS["cases"]))
def test_lint_agrees_with_real_mermaid(name):
    case = _CORPUS["cases"][name]
    raw_errors = lint_mermaid(case["code"])
    assert (raw_errors == []) == case["valid"], (
        f"原樣：Mermaid={'OK' if case['valid'] else '錯誤'}，lint={raw_errors}"
    )
    fixed_errors = lint_mermaid(fix_mermaid(case["code"]))
    assert (fixed_errors == []) == case["valid_after_fix"], f"修正後：lint={fixed_errors}"


def test_fix_never_breaks_valid_diagrams():
    for name, case in _CORPUS["cases"].items():
        if case["valid"]:
            assert case["valid_after_fix"], name
