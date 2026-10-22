# AGENTS.md

給 AI coding agent 看的專案說明。功能和使用方式見 [README.md](README.md)，架構和設計決定見 [docs/architecture.md](docs/architecture.md)。

## 專案概要

- SAgent：用網頁撰寫、管理系統分析文件（Spec），由本機 Ollama 模型協助撰寫。
- 後端 `backend/`：Python 3.11 以上、FastAPI、SQLAlchemy、SQLite。用 uv 管理套件。
- 前端 `frontend/`：React 19、Vite 8、TypeScript、TanStack Query、CodeMirror、Mermaid 12。
- 前端打包到 `frontend/dist`，由後端直接提供。執行時不需要 Node.js。
- Git repo：https://github.com/ZixuanHuang1995/SAgent ，主分支 `main`。使用者沒要求前，不要 commit 或 push。

## 常用指令

都在專案根目錄執行，除非另外寫明。

| 做什麼 | 指令 |
|---|---|
| 啟動（接 Ollama） | `.\scripts\start.ps1` |
| 示範模式（假模型） | `.\scripts\start.ps1 --fake-llm --data-dir data-demo` |
| 後端測試 | `cd backend; uv run pytest` |
| 後端 lint | `cd backend; uv run ruff check sagent tests ../scripts` |
| 前端型別檢查 | `cd frontend; npm run typecheck` |
| 前端打包 | `cd frontend; npm run build`（會先跑型別檢查） |
| 重新產生示範資料（真模型，7～8 小時） | `uv run --project backend python scripts/seed_demo.py --data-dir data-demo-build`，確認後改名成 `data-demo` |
| 用真模型跑一次 AI 流程 | `uv run --project backend python scripts/run_pipeline.py --requirement-file 需求.txt --out 輸出.md` |

## 改完程式要做的檢查

| 改了什麼 | 要做 |
|---|---|
| 後端 | `uv run pytest` 和 `uv run ruff check`，都要通過 |
| 前端 | `npm run build`。後端提供的是 `dist`，沒有重新打包，畫面不會更新 |
| 畫面 | 用 `.claude/launch.json` 的 `sagent-demo` 開預覽（埠 8766、假模型、資料在 `data-demo/`）實際操作確認 |
| 提示詞、agent 流程 | pytest 用假模型，只能測流程。要看真模型的結果用 `run_pipeline.py`，但 CPU 上一份文件要 30～70 分鐘，先問使用者再跑 |
| 圖表語法規則（`diagram_kinds.py`、`services/diagrams.py`） | 跑 `tests/test_diagrams.py`。它用前端同版本 Mermaid 產生的標準答案比對 |
| 升級 Mermaid | 用 `frontend/scripts/mermaid-validate.mjs` 重新產生 `backend/tests/data/mermaid_cases.json`，再跑測試 |

## 程式在哪裡

後端 `backend/sagent/`：

| 位置 | 內容 |
|---|---|
| `main.py` | `create_app`、`X-SAgent-Client` 標頭檢查、Host 檢查、提供前端檔案 |
| `config.py`、`cli.py` | 環境變數和命令列參數 |
| `models.py`、`migrations.py` | 資料表、舊資料庫升級 |
| `api/` | API 路由，保持精簡，邏輯放 `services/` |
| `services/` | 版本控制、基準版本、匯出、差異、圖表檢查、PlantUML |
| `agents/` | `planner.py`、`writer.py`、`reviewer.py`、`pipeline.py`、`runner.py`、`prompts.py`、`diagram_kinds.py`、`fake_llm.py`、`zh.py` |
| `doc_templates/` | 內建文件範本（YAML） |

前端 `frontend/src/`：

| 位置 | 內容 |
|---|---|
| `api.ts` | 所有 API 的型別和呼叫。`request()` 會自動加 `X-SAgent-Client` 標頭 |
| `queries.ts` | TanStack Query 的 hooks 和 query key |
| `components/ui.tsx` | 共用元件：`Modal`、`ConfirmDialog`、`useToast`、`Badge`、`Field`、`Spinner`、`Skeleton`、`Empty`、`Splitter`。新畫面先用這些 |
| `components/icons.tsx` | `Icon` 元件。新圖示加在 `PATHS` |
| `format.ts` | 狀態的中文名稱、時間格式 |
| `theme.ts`、`styles.css` | 顏色主題 |
| `pages/`、`hooks/useRunStream.ts` | 頁面、訂閱 AI 進度（SSE） |

## 慣例

### 語言

- 畫面文字、錯誤訊息、程式註解、文件都用繁體中文。句子簡單直白，不要花俏或文言。技術名詞可以用英文。
- 不能出現簡體字。模型輸出的簡體字由 `agents/zh.py` 處理，不要改成整段用 OpenCC 轉換（會把「干擾」「通過」改壞）。

### 後端

- 服務層出錯時丟 `services/errors.py` 的例外：`NotFound`（404）、`Conflict`（409）、`BadRequest`（400）、`Unavailable`（503）。`main.py` 會轉成 `{"detail": "中文訊息"}`。訊息是給使用者看的。
- 修改資料的 API（POST、PUT、PATCH、DELETE）都要有 `X-SAgent-Client` 標頭，這是 CSRF 防護，不要拿掉。
- 資料庫：新資料表啟動時自動建立。舊資料表加欄位寫在 `migrations.py`，每個步驟都要能重複執行。
- 版本：`spec_versions` 建立後不能修改，只有標籤可以改。還原是把舊內容存成新版本。存檔要帶根據的版本號，衝突回 409。
- AI 工作一次只跑一個，同一份 Spec 同時只能有一個。
- ruff 設定：行長 110、目標 Python 3.11。

### 前端

- 顏色只用 `styles.css` 的 CSS 變數。深色模式靠 `data-theme` 和 `prefers-color-scheme`，新樣式在淺色、深色都要看得清楚。
- 字級只用 `--fs-xs`～`--fs-2xl`（12、13、15、17、21、27px），不要用小數字級。字重只用 `--fw-normal`、`--fw-semibold`、`--fw-bold`。
- 中文字型優先用思源黑體（`Noto Sans TC`，Windows 11 新版內建），沒有才用微軟正黑體。微軟正黑體只有 400 和 700，所以不要依賴 500 這類中間字重。
- 等寬字型用 `var(--font-mono)`，並加 `font-variant-ligatures: none`，Mermaid 的 `-->` 才不會顯示成箭頭。
- 呼叫 API 一律經過 `api.ts`，資料讀取用 `queries.ts` 的 hooks。

### AI 代理

- 模型是在 CPU 上跑的 8B 左右小模型，又慢又容易出錯。改提示詞或流程前，先讀 `docs/architecture.md` 第 4.2 節「為了小模型做的設計」。
- 不要用 LangChain 之類的框架，代理是自己實作的。
- 文件的寫作規則有三種，由範本的 `style` 決定：`spec` 規格文件（只寫需求有的內容）、`advice` 建議類文件（例如 UI/UX 設計建議，可以提出需求沒寫的做法）、`idea` 構想文件（不寫細節規格，只有「可能的做法」一節可以提建議）。三種的寫作規則不同，見 `docs/architecture.md` 第 4.6、4.7 節。改其中一種的寫作規則時，不要動到其他種。
- Reviewer 只找格式、邏輯錯誤（交給 Writer 修）和需要人工覆核的地方（只列給使用者），不評論完整度、不打分數。格式問題盡量用程式檢查。三種文件共用審查提示詞 `reviewer_system()`，見 `docs/architecture.md` 第 4.8 節。
- 新增 Spec 時先選類型（範本的 `category`：idea、feature、design、change），類型清單在 `doc_templates.py` 的 `CATEGORIES`。新增內建範本要寫 `category`。
- 範本 `allow_diagrams: false` 表示不畫圖，`api/agent.py` 建立工作時和 `pipeline.py` 開始時都會強制關掉畫圖。
- Mermaid 12 的 `usecase-beta` 沒有官方文件，語法是從套件的解析器反推、再用 `mermaid-validate.mjs` 驗證的。

## 測試

- 測試不連 Ollama，全部用 `FakeLLM`。
- `backend/tests/conftest.py` 的 fixture：
  - `cfg`：資料放在暫存目錄、假模型、沒有延遲
  - `client`：已經帶 `X-SAgent-Client` 標頭的 `TestClient`；建立過的 `FakeLLM` 會記在 `fake_llms`
  - `slow_client`：假模型每次呼叫停 0.2 秒，測取消和排隊用
  - `project`：建好一個專案
  - `wait_run()`、`wait_status()`：等 AI 工作結束或進入某個狀態
- 前端沒有單元測試，靠型別檢查和瀏覽器預覽。

## 環境注意事項

- Windows，PowerShell 5.1。`.ps1` 檔要存成 UTF-8 with BOM，不然中文會變亂碼，還會出現語法錯誤。
- 這台沒有 GPU，Ollama 只能用 CPU。不要隨便跑真模型。
- 這台沒有 Java，PlantUML 還沒設定。
- 這是地端系統，資料不能送到外部服務（例如公開的 PlantUML 伺服器）。
- `data/` 是使用者的正式資料，不要刪改。測試和示範用暫存目錄或 `data-demo/`。`data-demo/` 是 `scripts/seed_demo.py` 用真模型產生的，花了好幾個小時，不要隨便覆蓋；畫面上測試新增的資料不影響。
- 8765 埠常被使用者自己開的 SAgent 占用，預覽用 8766。
- `index.html` 回應設定為 no-cache，`assets/` 檔名有雜湊。前端更新後，使用者第一次開頁面可能要按 Ctrl+F5。

## 文件維護

- 功能、指令、設定改了：更新 `README.md`
- 架構、資料表、API 改了：更新 `docs/architecture.md`
- 開發指令、慣例、注意事項改了：更新這份 `AGENTS.md`
