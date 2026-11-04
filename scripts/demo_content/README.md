# 示範資料的內容檔

`seed_demo.py --script` 用的內容檔。腳本模型（`scripts/demo_llm.py`）照這些檔案回答 SAgent 真正的 AI 流程（Planner → Writer → Reviewer），所以 AI 工作紀錄、大綱、審查結果、版本都跟實際操作一樣，只是幾秒就跑完。

改完內容檔先跑檢查：

```powershell
uv run --project backend python scripts/check_demo_content.py scripts/demo_content
uv run --project backend python scripts/check_demo_content.py scripts/demo_content --only FIX --keys idea,feature
```

## 1. 檔案放在哪裡

`<專案代碼>/<key>.json`。專案代碼和每份文件的需求內容在 `scripts/demo_scenarios.py`。

| 專案代碼 | 專案 | key |
|---|---|---|
| `FIX` | 設備報修 | `idea`、`feature`、`architecture`、`database`、`change`、`test_plan`、`revise` |
| `PUR` | 採購請購 | `idea`、`feature`、`architecture`、`database`、`change`、`revise` |
| `ROOM` | 會議室與訪客預約 | 同上 |
| `CS` | 客服工單 | 同上 |
| `SUDOKU` | 數獨解題與出題 | `solver`、`generator`、`revise` |

key 對應的文件類型（內建範本都是章節固定，內容檔的章節標題和順序要跟範本一樣）：

| key | 文件類型 |
|---|---|
| `idea` | 構想提案（`idea_proposal`） |
| `feature` | 功能規格書（`functional_spec`） |
| `architecture` | 系統架構設計 |
| `database` | 資料庫設計 |
| `change` | 變更需求說明 |
| `test_plan` | 測試計畫（用 `seed_demo.py` 裡的自訂範本 `TEST_PLAN_TEMPLATE`，只有 `showcase` 的專案有） |
| `solver`、`generator` | 演算法設計 |
| `revise` | 修訂模式，修訂哪一份看 `demo_scenarios.py` 的 `revise_target` |

## 2. 撰寫模式的檔案

`revise` 以外的 key 都是撰寫模式，有四個欄位：

```json
{
  "plan": {
    "title": "文件標題",
    "summary": "一兩句說明",
    "sections": [
      {
        "heading": "背景與問題",
        "goal": "這一節要寫什麼",
        "key_points": ["要點一", "要點二"],
        "diagrams": [{"kind": "flowchart", "title": "報修單處理流程", "description": "一句說明"}]
      }
    ],
    "open_questions": ["要跟客戶確認的問題"]
  },
  "sections": ["第 1 節的內文", "第 2 節的內文"],
  "diagrams": ["flowchart TD\n    A[\"開始\"] --> B[\"結束\"]"],
  "review": {
    "issues": [],
    "review_points": [{"section_id": "S2", "item": "要確認的內容", "reason": "為什麼要確認"}],
    "summary": "審查的總結"
  }
}
```

| 欄位 | 規則 |
|---|---|
| `plan` | Planner 的回答，格式照 `prompts.py` 的 `PLAN_SCHEMA`。`terms` 可以不寫。章節 2～10 節，標題不加編號，每節最多 2 張圖。章節固定的範本，標題要跟範本一樣 |
| `plan.sections[].diagrams[].kind` | 用 `agents/diagram_kinds.py` 的 `KINDS`，例如 `flowchart`、`sequence`、`usecase`、`er` |
| `sections` | 每一節的內文，順序和數量跟 `plan.sections` 一樣。開頭不寫章節標題，小標題用 `###`。內文不放圖 |
| `diagrams` | 大綱裡所有的圖，照章節順序排，數量要一樣。只寫 Mermaid 程式碼，不包 ` ``` `。腳本模型用大綱的圖標題對應，對不到才照順序 |
| `review` | 第一次審查的回答。`issues` 只能放 `low`（high、medium 會觸發修訂，腳本模型不支援）；`review_points` 2～5 項；`section_id` 寫 `S1`、`S2`…，是章節的順序。修訂後的再審查一律回答沒有錯誤 |

`issues` 每一項的欄位：`section_id`、`kind`（`format`、`conflict`、`requirement`、`unstated`）、`severity`、`problem`、`suggestion`。

## 3. 修訂模式的檔案（`revise.json`）

```json
{
  "plan": {
    "summary": "這次修訂改了什麼",
    "changes": [
      {"heading": "業務規則", "action": "update", "instruction": "BR-01 改成 30 分鐘"}
    ],
    "new_sections": [
      {"after": "業務規則", "heading": "新章節", "goal": "…", "key_points": ["…"], "diagrams": []}
    ],
    "open_questions": ["…"]
  },
  "updates": {"業務規則": "修改後的整節內文"},
  "new_sections": {"新章節": "新章節的內文"},
  "diagrams": [],
  "review": {"issues": [], "review_points": [], "summary": "…"}
}
```

| 欄位 | 規則 |
|---|---|
| `plan` | 格式照 `REVISE_PLAN_SCHEMA`，但 `changes` 用章節標題 `heading`，不用 `section_id`。腳本模型會換成提示詞裡的 E1、E2… |
| `plan.changes[].action` | `update` 或 `remove`。`heading` 要是原文件的章節 |
| `plan.new_sections[].after` | 原文件的章節標題，或 `END` |
| `updates` | `action` 是 `update` 的每一節修改後的內文。原本那一節有圖的話要保留（可以有 ` ```mermaid `），張數不能變少 |
| `new_sections` | 新章節的內文，key 是章節標題 |
| `diagrams` | 新章節的圖，數量跟 `plan.new_sections` 的圖一樣 |
| `review` | 同撰寫模式 |

## 4. 內文的限制

`check_demo_content.py` 會檢查：

- 沒有簡體字。`agents/zh.py` 會把「峰」「群」改成「峯」「羣」，所以內容檔避開這兩個字
- 沒有圖片語法 `![]()`、「（註：…）」這類內部說明、`<br>`
- 每張圖用前端同版本的 Mermaid 檢查（`frontend/scripts/mermaid-validate.mjs`，要有 Node.js）
- 用腳本模型實際跑一次 AI 流程：大綱不能改用範本章節、不能有警告、程式審查不能找到 high 或 medium 的錯誤（例如表格欄數不對、引用沒定義的編號），實際畫的圖數要跟 `diagrams` 一樣

範本的章節改了，對應的內容檔也要改。章節固定的範本，內容檔少一節就會失敗。
