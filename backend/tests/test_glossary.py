from sagent.services.glossary import extract_terms, suggestions

DOC = """# 報修申請

## 1. 名詞定義

| 名詞 | 說明 |
|---|---|
| **報修人** | 提出報修的員工 |
| 報修單 | 一筆報修資料 |
| SSO | 單一登入 |
| — | 不是名詞 |

## 2. 角色

| 角色 | 可以做的事 |
|---|---|
| 報修人 | 提出報修 |
| 總務窗口 | 派工 |

## 3. 流程

- 處理：建立報修單，狀態為「已提交」。
- 處理：接單後狀態改為處理中；回報後狀態改為「待確認」。
- 狀態由處理中改為待確認這種寫法不算新的狀態，整句話也不會被當成狀態。

```mermaid
stateDiagram-v2
    state "已結案" as Closed
    [*] --> 已提交
    已提交 --> 處理中 : 派工
    處理中 --> Closed
```

```text
| 名詞 | 說明 |
|---|---|
| 程式碼裡的表格 | 不算 |
```

## 4. 資料

| 實體 | 說明 |
|---|---|
| REPAIR_REQUEST | 報修單，是整個系統的核心 |
| DISPATCH | 派工紀錄，每次派工留一筆 |
| employee | employee |
"""


def test_extract_terms_from_tables_text_and_state_diagram():
    terms = {t["name"]: t for t in extract_terms(DOC)}
    # 名詞定義的報修人，在角色表裡出現，種類是角色；說明用名詞定義的
    assert terms["報修人"]["kind"] == "角色" and terms["報修人"]["note"] == "提出報修的員工"
    assert terms["總務窗口"]["kind"] == "角色"
    assert terms["SSO"]["kind"] == "其他"
    # 內文和狀態圖的狀態，狀態圖的 state "名稱" as 代號 用名稱
    states = [t["name"] for t in extract_terms(DOC) if t["kind"] == "狀態"]
    assert states == ["已提交", "處理中", "已結案", "待確認"]
    # 實體表的英文代號改用說明開頭的中文名稱；報修單在名詞定義已經有，種類照實體表算資料
    assert terms["派工紀錄"] == {"name": "派工紀錄", "kind": "資料", "note": "每次派工留一筆"}
    assert terms["報修單"]["kind"] == "資料" and terms["報修單"]["note"] == "一筆報修資料"
    assert not {"—", "程式碼裡的表格", "REPAIR_REQUEST", "employee"} & set(terms)
    # 角色、狀態、資料、其他依序排
    kinds = [t["kind"] for t in extract_terms(DOC)]
    assert kinds == sorted(kinds, key=["角色", "狀態", "資料", "其他"].index)


def test_suggestions_compare_with_project_glossary():
    glossary = [
        {"name": "報修人", "kind": "角色", "note": "提出報修的員工。"},  # 只差標點：一樣
        {"name": "總務窗口", "kind": "角色", "note": "總務人員"},  # 說明不一樣
        {"name": "已提交", "kind": "其他", "note": ""},  # 種類不一樣
    ]
    result = {s.name: s for s in suggestions(glossary, DOC)}
    assert "報修人" not in result
    assert result["總務窗口"].status == "changed" and result["總務窗口"].current_note == "總務人員"
    assert result["總務窗口"].note == "派工"
    # 文件沒有說明時保留詞彙表原本的說明
    assert result["已提交"].status == "changed" and result["已提交"].kind == "狀態"
    assert result["SSO"].status == "new"
    # 文件只在名詞定義表寫了（種類是「其他」），不會把詞彙表的「角色」蓋掉
    only_terms = "| 名詞 | 說明 |\n|---|---|\n| 報修人 | 提出報修的人 |\n"
    [s] = suggestions([{"name": "報修人", "kind": "角色", "note": "員工"}], only_terms)
    assert (s.kind, s.note, s.status) == ("角色", "提出報修的人", "changed")


def test_glossary_suggestions_api(client, project):
    pid = project["project"]["id"]
    client.patch(
        f"/api/projects/{pid}", json={"glossary": [{"name": "報修人", "kind": "角色", "note": "員工"}]}
    )
    body = {"phase_id": project["phases"][0]["id"], "title": "報修", "init": "none"}
    spec = client.post(f"/api/projects/{pid}/specs", json=body).json()
    r = client.get(f"/api/specs/{spec['id']}/glossary-suggestions")
    assert r.status_code == 400  # 還沒有內容
    client.put(f"/api/specs/{spec['id']}/content", json={"content": DOC, "base_version_no": 0})
    data = client.get(f"/api/specs/{spec['id']}/glossary-suggestions").json()
    assert data["version_no"] == 1 and data["glossary_count"] == 1
    by_name = {s["name"]: s for s in data["suggestions"]}
    assert by_name["報修人"]["status"] == "changed" and by_name["報修人"]["current_note"] == "員工"
    assert by_name["已提交"]["status"] == "new"
    # 只是建議，不會寫進專案詞彙表
    tree = client.get(f"/api/projects/{pid}").json()
    assert [t["name"] for t in tree["project"]["glossary"]] == ["報修人"]


def test_states_from_lists_columns_and_transitions():
    """C-12：內文列舉、其他表格的狀態欄、狀態轉換也要抽到。"""
    doc = """## 1. 流程

工單狀態依序為：新建、已分派、處理中（工程師處理）、待客戶回覆、已解決、已結案。

依「草稿、待簽核」的順序管理狀態。

狀態轉換：已提交 → 處理中 → 待確認。客服可以把工單從「已解決」改為「處理中」，也可以從暫停改為作廢。

登入後首頁 → 我的工單（沒有提到那個詞，不算）

## 2. 轉換規則

| 目前狀態 | 動作 | 新狀態 |
|---|---|---|
| 待簽核 | 主管退回 | 已退回 |
| 任何狀態 | 管理員取消 | 已取消 |
"""
    states = {t["name"] for t in extract_terms(doc) if t["kind"] == "狀態"}
    expected = {"新建", "已分派", "處理中", "待客戶回覆", "已解決", "已結案", "草稿", "待簽核", "已提交", "待確認"}
    assert expected | {"已退回", "已取消", "暫停", "作廢"} <= states
    assert not {"任何狀態", "我的工單", "登入後首頁", "工程師處理"} & states


def test_states_in_demo_functional_spec():
    """客服工單的功能規格書：狀態寫在內文的列舉裡，沒有狀態表（之前只抽到 3 個）。"""
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).parents[2] / "scripts/demo_content/CS/feature.json").read_text(encoding="utf-8"))
    doc = "\n\n".join(f"## {s['heading']}\n{body}" for s, body in zip(data["plan"]["sections"], data["sections"], strict=True))
    states = {t["name"] for t in extract_terms(doc) if t["kind"] == "狀態"}
    assert {"新建", "已分派", "處理中", "待客戶回覆", "已解決", "已結案"} <= states
