"""產生示範資料：四個不同情境的完整專案，加上一個 Algorithm 類型的專案，AI 撰寫的部分用真的地端模型。

四個專案（情境和需求內容在 demo_scenarios.py）：
- 設備報修系統（主打）、採購請購簽核系統、會議室與訪客預約系統、客服工單與知識庫系統

每個專案都走完整流程：構想 → 轉成功能規格（來源追溯）→ 使用案例 → UI/UX 建議 → SDD 五節 → 系統架構 →
資料庫 → API → 依需求修訂 → 第二期變更。九種文件類型、四種類型、四種狀態都有，另外有匯入 Markdown、範本骨架、
手動編輯、版本標籤、封存、兩到三個基準版本（含只收一個階段的）、每個階段的說明。

各專案另外強調不同的功能：
- 設備報修：AI 工作失敗（選了沒安裝的模型）、中途取消、範本骨架接著 AI 撰寫、手動編輯後還原、自訂範本「測試計畫」、
  手動插入類別圖、一份構想轉出兩份文件
- 採購請購：修訂 SDD 五節、資料庫加暫存欄位再還原、不自動修訂的 AI 工作
- 會議室與訪客預約：修訂使用案例、設計審查後才新增「第二期」階段、還沒有版本的 Spec、圖表只用 Mermaid
- 客服工單：修訂 UI/UX 建議（建議類文件）、不自動修訂的 AI 工作、只收需求萃取階段的基準版本

另外一個 Algorithm 類型的專案，流程比較短（seed_algorithm_project）：
- 數獨解題與出題引擎：匯入規則說明 → AI 撰寫解題、出題兩份演算法設計 → 基準版本 → 依玩家測試的需求修訂解題演算法

模型和 AI 設定預設跟 SAgent 目前的設定一樣（從 data/sagent.db 唯讀讀取），可以用 --model 覆寫。

整個流程透過 API 進行（在程式內啟動 app，不用另外開伺服器），AI 工作紀錄、步驟事件都會存進資料庫，
跟使用者在網頁上操作的結果一樣。

可以隨時停止（Ctrl+C 或直接關掉視窗），再執行一次會從停下的地方繼續：已經完成的文件和版本都存在資料庫裡，
已經做過的步驟會跳過。停下時寫到一半的那份會重寫，它的 AI 工作紀錄在下次啟動時刪掉，不會在示範資料裡留下
「已取消」或「被中斷」的工作。

用法（在專案根目錄）：
    uv run --project backend python scripts/seed_demo.py --data-dir data-demo-build
    uv run --project backend python scripts/seed_demo.py --data-dir data-demo-build --model qwen3:8b
    uv run --project backend python scripts/seed_demo.py --data-dir 暫存目錄 --fake   # 用假模型檢查腳本
    uv run --project backend python scripts/seed_demo.py --data-dir data-demo-build --script scripts/demo_content

CPU 上用 qwen3.6:35b-a3b 全部跑完大約要 29～34 小時（一份文件 20～80 分鐘，總共約 39 份）。

--script：用腳本模型（demo_llm.py）把事先寫好的內容檔回答給 AI 流程，幾分鐘就跑完。內容檔的格式見
DEMO_DATA_PLAN.md。AI 工作紀錄的模型名稱還是 SAgent 設定的模型。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from demo_llm import MISSING_MODELS, ContentBook
from demo_scenarios import ALGO_SCENARIOS, SCENARIOS, AlgoScenario, Scenario
from fastapi.testclient import TestClient

from sagent.config import ROOT_DIR, load_config
from sagent.main import create_app

STARTED = time.monotonic()

# 從 SAgent 設定複製到示範資料的 AI 設定
AI_SETTINGS = (
    "ollama_url",
    "default_model",
    "temperature",
    "max_revisions",
    "uml_syntax",
    "diagrams",
    "num_ctx",
)

# 各種文件的 AI 選項。圖的數量比預設（4 張）少，CPU 上比較快
RUN_OPTIONS = {
    "idea_proposal": {"max_diagrams": 1},
    "functional_spec": {"max_diagrams": 3},
    "use_case": {"max_diagrams": 3},
    "uiux_advice": {},
    "sdd_five": {"max_diagrams": 2},
    "architecture": {"max_diagrams": 3},
    "database": {"max_diagrams": 2},
    "api_spec": {"max_diagrams": 1},
    "change_request": {"max_diagrams": 1},
    "test_plan": {"max_diagrams": 1},
    "algorithm_design": {"max_diagrams": 2},
    "experiment_design": {"max_diagrams": 2},
}

# 自訂範本：示範「設定」頁的自訂範本，以及用自訂範本讓 AI 撰寫
TEST_PLAN_TEMPLATE = """key: test_plan
name: 測試計畫
description: 安排驗收測試的範圍、環境、重點情境、驗收標準和時程。
order: 20
category: design
sections:
  - heading: 測試範圍
    goal: 列出這次要測試和不測試的功能。
  - heading: 測試環境與資料
    goal: 說明測試用的環境、帳號和測試資料。
  - heading: 測試情境
    goal: 用表格列出重點測試情境，編號 TC-01、TC-02…，每個情境寫前置條件、操作步驟和預期結果。
  - heading: 驗收標準
    goal: 說明什麼情況算驗收通過，以及錯誤的分級和處理方式。
  - heading: 時程與分工
    goal: 列出各階段的日期和負責單位。
    diagrams: [gantt]
"""

# 文件類型 → 內容檔的 key（--script 用，修訂一律是 revise）
SCRIPT_KEYS = {
    "idea_proposal": "idea",
    "functional_spec": "feature",
    "use_case": "use_case",
    "uiux_advice": "uiux",
    "sdd_five": "sdd",
    "architecture": "architecture",
    "database": "database",
    "api_spec": "api",
    "change_request": "change",
    "test_plan": "test_plan",
}


def log(text: str) -> None:
    elapsed = time.monotonic() - STARTED
    print(
        f"[{int(elapsed // 3600):d}:{int(elapsed % 3600 // 60):02d}:{int(elapsed % 60):02d}] {text}",
        flush=True,
    )


def sagent_settings() -> dict:
    """SAgent 目前的 AI 設定（唯讀讀取正式資料庫）。沒有資料庫時用預設值。"""
    cfg = load_config()
    settings = {"ollama_url": cfg.ollama_url, "default_model": cfg.default_model}
    if cfg.db_path.exists():
        con = sqlite3.connect(f"{cfg.db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM settings WHERE key = 'app'").fetchone()
        except sqlite3.Error:
            row = None
        finally:
            con.close()
        if row:
            saved = json.loads(row[0])
            settings.update({k: saved[k] for k in AI_SETTINGS if k in saved})
    return settings


# ---------------------------------------------------------------------------
# API 小工具
# ---------------------------------------------------------------------------


class Seeder:
    def __init__(
        self, client: TestClient, model: str, fake: bool, marker: Path, script: ContentBook | None = None
    ):
        self.c = client
        self.model = model
        self.fake = fake
        self.script = script
        self.code = ""  # 目前的專案代碼，告訴腳本模型用哪個內容檔
        self.poll = 0.05 if fake or script else 5.0
        # 記下執行中的 AI 工作編號；中途停掉的話，下次啟動時把這筆刪掉（見 clean_interrupted）
        self.marker = marker

    def call(self, method: str, url: str, **kw):
        resp = self.c.request(method, url, **kw)
        if resp.status_code >= 400:
            raise RuntimeError(f"{method} {url} 失敗：{resp.status_code} {resp.text}")
        return resp.json() if resp.content else None

    # --- 專案與 Spec ---

    def project(self, sc: Scenario) -> tuple[int, dict[str, int]]:
        found = next((p for p in self.call("GET", "/api/projects") if p["name"] == sc.name), None)
        if found is None:
            body = {
                "name": sc.name,
                "code": sc.code,
                "description": sc.description,
                "phases": [n for n, _ in sc.phases],
            }
            found = self.call("POST", "/api/projects", json=body)
            log(f"建立專案「{sc.name}」")
        pid = found["id"] if "id" in found else found["project"]["id"]
        notes = dict(sc.phases)
        phases = {}
        for ph in self.call("GET", f"/api/projects/{pid}")["phases"]:
            phases[ph["name"]] = ph["id"]
            if ph["name"] in notes and ph["description"] != notes[ph["name"]]:
                self.call("PATCH", f"/api/phases/{ph['id']}", json={"description": notes[ph["name"]]})
        return pid, phases

    def add_phase(self, pid: int, name: str, description: str) -> int:
        for ph in self.call("GET", f"/api/projects/{pid}")["phases"]:
            if ph["name"] == name:
                return ph["id"]
        log(f"新增階段「{name}」")
        return self.call(
            "POST", f"/api/projects/{pid}/phases", json={"name": name, "description": description}
        )["id"]

    def find_spec(self, pid: int, title: str) -> dict | None:
        for archived in ("false", "true"):
            for sp in self.call("GET", f"/api/projects/{pid}/specs", params={"archived": archived}):
                if sp["title"] == title:
                    return self.call("GET", f"/api/specs/{sp['id']}")
        return None

    def spec(self, pid: int, phase_id: int, title: str, doc_type: str, init: str = "none", **kw) -> dict:
        found = self.find_spec(pid, title)
        if found:
            return found
        log(f"建立 Spec「{title}」（{doc_type}，{init}）")
        body = {"phase_id": phase_id, "title": title, "doc_type": doc_type, "init": init, **kw}
        return self.call("POST", f"/api/projects/{pid}/specs", json=body)

    def refresh(self, spec: dict) -> dict:
        return self.call("GET", f"/api/specs/{spec['id']}")

    def versions(self, spec: dict) -> list[dict]:
        return self.call("GET", f"/api/specs/{spec['id']}/versions")

    def runs(self, spec: dict) -> list[dict]:
        return self.call("GET", "/api/agent/runs", params={"spec_id": spec["id"]})

    # --- AI 工作 ---

    def start(self, spec: dict, mode: str, requirement: str, options: dict, key: str | None = None) -> dict:
        """key：內容檔的 key（--script 用），沒給時依文件類型決定，修訂一律是 revise。"""
        if self.script:
            key = "revise" if mode == "revise" else key or SCRIPT_KEYS[spec["doc_type"]]
            self.script.current = (self.code, key)
        self.wait_ollama()
        body = {"spec_id": spec["id"], "mode": mode, "requirement": requirement, "options": options}
        run = self.call("POST", "/api/agent/runs", json=body)
        self.marker.write_text(str(run["id"]), encoding="utf-8")
        return run

    def wait_ollama(self, limit: float = 2 * 3600) -> None:
        """Ollama 連不到時先等，不要送出注定失敗的工作（失敗紀錄刪不掉）。"""
        if self.fake or self.script:
            return
        waited = 0.0
        while not self.call("GET", "/api/health")["ollama"]["reachable"]:
            if waited >= limit:
                raise RuntimeError("Ollama 一直連不到，先停止。啟動 Ollama 後再執行一次會從這裡繼續")
            if waited % 300 == 0:
                log("連不到 Ollama，等它啟動（每 30 秒檢查一次）")
            time.sleep(30)
            waited += 30

    def run(
        self, spec: dict, mode: str, requirement: str, options: dict, retries: int = 1, key: str | None = None
    ) -> dict:
        options = {"model": self.model, **options}
        label = "撰寫" if mode == "draft" else "修訂"
        for attempt in range(retries + 1):
            run = self.start(spec, mode, requirement, options, key)
            log(f"AI {label}「{spec['title']}」開始（工作 #{run['id']}，選項 {options}）")
            result = self.wait(run["id"])
            if result["status"] == "succeeded":
                review = (result.get("review") or {}).get("final") or {}
                errors = sum(1 for i in review.get("issues", []) if i.get("severity") in ("high", "medium"))
                points = len(review.get("review_points", []))
                log(f"AI {label}「{spec['title']}」完成，剩下錯誤 {errors} 個、需要人工覆核 {points} 項")
                return self.refresh(spec)
            log(f"AI {label}「{spec['title']}」{result['status']}：{result.get('error') or ''}")
            if attempt < retries:
                log("重試一次")
        raise RuntimeError(f"「{spec['title']}」的 AI 工作沒有成功")

    def wait(self, run_id: int) -> dict:
        last = ""
        last_print = 0.0
        while True:
            run = self.call("GET", f"/api/agent/runs/{run_id}")
            if run["status"] in ("succeeded", "failed", "cancelled"):
                self.marker.unlink(missing_ok=True)
                return run
            steps = [e for e in run["events"] if e["type"] == "step_end"]
            msg = f"{steps[-1]['agent']}：{steps[-1]['message']}" if steps else "等待模型"
            now = time.monotonic()
            if msg != last or now - last_print > 300:
                log(f"  … {msg}")
                last, last_print = msg, now
            time.sleep(self.poll)

    def done(self, spec: dict, mode: str) -> bool:
        return any(r["mode"] == mode and r["status"] == "succeeded" for r in self.runs(spec))

    def draft(self, spec: dict, requirement: str, options: dict, key: str | None = None) -> dict:
        """AI 撰寫。這份 Spec 已經有成功的撰寫工作就跳過（範本骨架建立的 Spec 一開始就有 v1）。"""
        if self.done(spec, "draft"):
            return self.refresh(spec)
        return self.run(spec, "draft", requirement, options, key=key)

    def revise(self, spec: dict, requirement: str, options: dict) -> dict:
        """AI 修訂。這份 Spec 已經有成功的修訂工作就跳過。"""
        if self.done(spec, "revise"):
            return self.refresh(spec)
        return self.run(spec, "revise", requirement, options)

    def fail_once(self, spec: dict, requirement: str) -> None:
        """示範失敗的 AI 工作：選一個沒安裝的模型，Planner 第一次呼叫就會失敗，不會載入模型。"""
        if self.fake:
            log("假模型不會因為模型名稱失敗，跳過示範失敗的工作")
            return
        if any(r["status"] == "failed" for r in self.runs(spec)):
            return
        if self.script:
            model = MISSING_MODELS[0]  # 腳本模型遇到這些名稱會丟出跟 Ollama 一樣的錯誤
        else:
            installed = {m["name"] for m in self.call("GET", "/api/agent/models")["models"]}
            model = next(m for m in MISSING_MODELS if m not in installed)
        run = self.start(spec, "draft", requirement, {"model": model})
        result = self.wait(run["id"])
        log(
            f"示範失敗的 AI 工作 #{run['id']}（模型 {model}）：{result['status']}，{result.get('error') or ''}"
        )

    def cancel_after_plan(self, spec: dict, requirement: str, options: dict) -> None:
        """示範取消：大綱完成、開始寫第一節後取消。大綱和用量會留在工作紀錄裡。"""
        if any(r["status"] == "cancelled" for r in self.runs(spec)):
            return
        run = self.start(spec, "draft", requirement, {"model": self.model, **options})
        log(f"AI 撰寫「{spec['title']}」開始（工作 #{run['id']}），大綱完成後取消")
        while True:
            detail = self.call("GET", f"/api/agent/runs/{run['id']}")
            if detail["status"] in ("succeeded", "failed", "cancelled"):
                self.marker.unlink(missing_ok=True)
                log(f"工作 #{run['id']} 在取消前就結束了：{detail['status']}")
                return
            planned = any(
                e["type"] == "step_end" and e["agent"] == "planner" and (e.get("data") or {}).get("ok")
                for e in detail["events"]
            )
            if planned:
                break
            time.sleep(self.poll)
        self.call("POST", f"/api/agent/runs/{run['id']}/cancel")
        result = self.wait(run["id"])
        log(f"取消 AI 工作 #{run['id']}：{result['status']}")

    # --- 版本、狀態、基準版本 ---

    def edit(self, spec: dict, message: str, change) -> dict:
        """手動編輯。已經有同樣說明的版本就跳過。"""
        if any(v["message"] == message for v in self.versions(spec)):
            return self.refresh(spec)
        spec = self.refresh(spec)
        body = {
            "content": change(spec["content"]),
            "message": message,
            "base_version_no": spec["head_version_no"],
        }
        self.call("PUT", f"/api/specs/{spec['id']}/content", json=body)
        log(f"手動編輯「{spec['title']}」：{message}")
        return self.refresh(spec)

    def restore(self, spec: dict, version_no: int, message: str) -> dict:
        """還原到指定版本。已經有還原的版本就跳過。"""
        if any(v["source"] == "restore" for v in self.versions(spec)):
            return self.refresh(spec)
        self.call("POST", f"/api/specs/{spec['id']}/versions/{version_no}/restore", json={"message": message})
        log(f"還原「{spec['title']}」到 v{version_no}")
        return self.refresh(spec)

    def label(self, spec: dict, label: str, version_no: int | None = None) -> None:
        """在指定版本（預設最新版）加標籤。已經有版本用了這個標籤就跳過，重跑時才不會貼到別的版本。"""
        if any(v.get("label") == label for v in self.versions(spec)):
            return
        no = version_no or self.refresh(spec)["head_version_no"]
        self.call("PUT", f"/api/specs/{spec['id']}/versions/{no}/label", json={"label": label})

    def status(self, spec: dict, status: str) -> None:
        self.call("PATCH", f"/api/specs/{spec['id']}", json={"status": status})

    def archive(self, spec: dict) -> None:
        if not self.refresh(spec)["archived"]:
            self.call("POST", f"/api/specs/{spec['id']}/archive")
            log(f"封存「{spec['title']}」")

    def baseline(self, pid: int, name: str, note: str, phase_id: int | None = None) -> None:
        if any(b["name"] == name for b in self.call("GET", f"/api/projects/{pid}/baselines")):
            return
        self.call(
            "POST", f"/api/projects/{pid}/baselines", json={"name": name, "note": note, "phase_id": phase_id}
        )
        log(f"建立基準版本「{name}」")


# ---------------------------------------------------------------------------
# 文字處理
# ---------------------------------------------------------------------------


def insert_after_title(text: str, line: str) -> str:
    first, _, rest = text.partition("\n")
    return f"{first}\n\n{line}{rest}"


def last_section(text: str) -> tuple[str, int]:
    """最後一個 ## 章節的標題和開始位置。"""
    matches = list(re.finditer(r"^## +(.+)$", text, re.M))
    if not matches:
        return "", len(text)
    return matches[-1].group(1).strip(), matches[-1].start()


def first_agent_version(s: Seeder, spec: dict) -> int:
    """AI 撰寫產生的版本號（之後的修訂也是 AI 版本，所以取最小的）。"""
    return min(v["version_no"] for v in s.versions(spec) if v["source"] == "agent")


def interview_note(sc: Scenario) -> tuple[str, str]:
    """把功能規格的訪談補充整理成一份「需求訪談紀錄」，匯入後在基準版本之後封存。"""
    body = sc.texts["feature_extra"].strip().removeprefix("---").strip()
    head, _, items = body.partition("\n")
    m = re.search(r"（(\d{4}/\d{1,2}/\d{1,2})，(.+?)）", head)
    date, units = (m.group(1), m.group(2)) if m else ("", "")
    title = f"需求訪談紀錄 {date}".strip()
    content = f"# 需求訪談紀錄（{date}）\n\n> 參加單位：{units}。內容會整理進「{sc.titles['feature']}」。\n\n{items.strip()}\n"
    return title, content


# ---------------------------------------------------------------------------
# 流程
# ---------------------------------------------------------------------------


def seed_project(s: Seeder, sc: Scenario) -> int:
    t, x = sc.titles, sc.texts

    def opts(doc_type: str) -> dict:
        return {**RUN_OPTIONS.get(doc_type, {}), **sc.options.get(doc_type, {})}

    log(f"===== {sc.name} =====")
    s.code = sc.code
    pid, ph = s.project(sc)
    req, sys_ph, det = ph["需求萃取"], ph["系統分析"], ph["系統設計"]

    # 1. 匯入的文件：名詞表、舊流程說明（停用）、訪談紀錄（之後封存）
    glossary = s.spec(pid, req, t["glossary"], "functional_spec", init="content", content=x["glossary_md"])
    s.status(glossary, "approved")
    legacy = s.spec(pid, req, t["legacy"], "functional_spec", init="content", content=x["legacy_md"])
    s.status(legacy, "deprecated")
    note_title, note_md = interview_note(sc)
    interview = s.spec(pid, req, note_title, "functional_spec", init="content", content=note_md)

    # 2. 構想 → 主管核可
    idea = s.draft(s.spec(pid, req, t["idea"], "idea_proposal"), x["idea"], opts("idea_proposal"))
    s.label(idea, sc.labels["idea"], 1)
    s.status(idea, "approved")

    # 3. 構想轉成功能規格：需求 = 構想內容 + 訪談補充
    feature_req = idea["content"] + x["feature_extra"]
    init = "template" if sc.showcase else "none"
    feature = s.spec(pid, req, t["feature"], "functional_spec", init=init, source_spec_id=idea["id"])
    if sc.showcase:
        # 先選錯模型失敗一次、再中途取消一次，第三次才完整寫完
        s.fail_once(feature, feature_req)
        s.cancel_after_plan(feature, feature_req, opts("functional_spec"))
    feature = s.draft(feature, feature_req, opts("functional_spec"))
    if sc.showcase:
        # 手動整理時誤刪最後一節，再還原成 AI 初稿
        ai_no = first_agent_version(s, feature)
        s.label(feature, "AI 初稿", ai_no)
        ai_text = s.call("GET", f"/api/specs/{feature['id']}/versions/{ai_no}")["content"]
        heading, cut = last_section(ai_text)
        feature = s.edit(
            feature,
            f"加入會議待辦事項，刪除「{heading}」",
            lambda _: insert_after_title(ai_text[:cut].rstrip() + "\n\n" + x["todo_md"], x["owner_note"]),
        )
        feature = s.restore(feature, ai_no, f"「{heading}」還要保留，還原到 v{ai_no}")
    feature = s.edit(
        feature, "加入文件負責人和審查單位", lambda text: insert_after_title(text, x["owner_note"])
    )
    s.label(feature, sc.labels["feature_review"])
    s.status(feature, "reviewing")

    # 4. 其他需求萃取文件
    use_case = s.draft(s.spec(pid, req, t["use_case"], "use_case"), x["use_case"], opts("use_case"))
    s.status(use_case, "reviewing")
    uiux_kw = {"source_spec_id": idea["id"]} if sc.showcase else {}
    uiux = s.draft(s.spec(pid, req, t["uiux"], "uiux_advice", **uiux_kw), x["uiux"], opts("uiux_advice"))

    # 5. 需求確認：基準版本，之後封存訪談紀錄、補名詞表，比較時才會有「移除」和「變更」
    s.baseline(pid, *sc.baselines[0])
    if sc.phase_baseline:
        s.baseline(pid, *sc.phase_baseline, phase_id=req)
    s.archive(interview)
    s.edit(glossary, "補充名詞", lambda text: text.rstrip("\n") + "\n" + sc.glossary_row + "\n")

    # 6. 系統分析、系統設計
    sdd = s.draft(s.spec(pid, det, t["sdd"], "sdd_five"), x["sdd"], opts("sdd_five"))
    s.status(sdd, "approved")
    arch = s.spec(pid, sys_ph, t["architecture"], "architecture", init="template")
    arch = s.draft(arch, x["architecture"], opts("architecture"))
    s.status(arch, "reviewing")
    db = s.draft(s.spec(pid, sys_ph, t["database"], "database"), x["database"], opts("database"))
    if sc.db_edit == "class":
        s.edit(db, "補充物件模型（類別圖）", lambda text: text.rstrip("\n") + "\n\n" + x["db_class"])
    elif sc.db_edit == "temp_restore":
        ai_no = first_agent_version(s, db)
        s.edit(db, "加入暫存欄位", lambda text: text.rstrip("\n") + "\n\n" + x["db_temp"])
        s.restore(db, ai_no, f"暫存欄位不需要，還原到 v{ai_no}")
    api = s.draft(s.spec(pid, det, t["api"], "api_spec"), x["api"], opts("api_spec"))
    s.status(api, "reviewing")
    s.spec(pid, det, sc.skeleton[0], sc.skeleton[1], init="template")
    if sc.pending:
        phase, title, doc_type = sc.pending
        s.spec(pid, ph[phase], title, doc_type, init="none")

    # 7. 依客戶補充修訂
    targets = {"feature": feature, "use_case": use_case, "uiux": uiux, "sdd": sdd, "api": api}
    target = targets[sc.revise_target]
    target = s.revise(target, x["revise"], opts(target["doc_type"]))
    s.label(target, sc.labels["revised"])
    s.status(target, "approved")

    s.baseline(pid, *sc.baselines[1])

    # 8. 第二期變更、驗收測試
    phase2 = s.add_phase(pid, *sc.late_phase) if sc.late_phase else ph["第二期"]
    s.draft(s.spec(pid, phase2, t["change"], "change_request"), x["change"], opts("change_request"))
    if sc.showcase:
        plan = s.spec(pid, ph["驗收測試"], t["test_plan"], "test_plan")
        s.draft(plan, x["test_plan"], opts("test_plan"))
    return pid


def seed_algorithm_project(s: Seeder, sc: AlgoScenario) -> int:
    """Algorithm 類型的專案：匯入說明文件 → AI 撰寫演算法或實驗設計 → 基準版本 → 依需求修訂其中一份。"""
    t, x = sc.titles, sc.texts
    log(f"===== {sc.name} =====")
    s.code = sc.code
    pid, ph = s.project(sc)
    phase = ph[sc.phases[0][0]]

    rules = s.spec(pid, phase, t["rules"], sc.rules_doc_type, init="content", content=x["rules_md"])
    s.status(rules, "approved")
    docs = {}
    for key, doc_type in sc.doc_types.items():
        opts = {**RUN_OPTIONS.get(doc_type, {}), **sc.options.get(doc_type, {})}
        docs[key] = s.draft(s.spec(pid, phase, t[key], doc_type), x[key], opts, key=key)
        s.label(docs[key], sc.labels["draft"])
        s.status(docs[key], "reviewing")
    s.baseline(pid, *sc.baseline)

    target = docs[sc.revise_target]
    opts = {**RUN_OPTIONS.get(target["doc_type"], {}), **sc.options.get(target["doc_type"], {})}
    target = s.revise(target, x["revise"], opts)
    s.label(target, sc.labels["revised"])
    s.status(target, "approved")
    return pid


def seed(s: Seeder, settings: dict) -> None:
    s.call("PUT", "/api/settings", json={**settings, "default_model": s.model, "plantuml_server": ""})
    pids = [seed_project(s, sc) for sc in SCENARIOS]
    pids += [seed_algorithm_project(s, sc) for sc in ALGO_SCENARIOS]
    # 首頁依更新時間排序：倒著更新一次，第一個情境排最前面
    for pid in reversed(pids):
        s.call("PATCH", f"/api/projects/{pid}", json={})
    left = [
        r
        for r in s.call("GET", "/api/agent/runs", params={"limit": 200})
        if r["status"] in ("queued", "running")
    ]
    if left:
        raise RuntimeError(f"還有沒結束的 AI 工作：{[r['id'] for r in left]}")


def clean_interrupted(db_path: Path, marker: Path) -> None:
    """刪掉上次中途停掉的 AI 工作紀錄，示範資料裡才不會留下「已取消」或「被中斷」的工作。

    已經完成的文件和版本都存在資料庫裡，不受影響；被中斷的那份下次會重新撰寫。
    要在啟動 app 之前做，不然 app 啟動時會先把執行中的工作標成失敗。
    """
    if not db_path.exists():
        marker.unlink(missing_ok=True)
        return
    con = sqlite3.connect(db_path)
    try:
        ids = {r[0] for r in con.execute("SELECT id FROM agent_runs WHERE status IN ('queued', 'running')")}
        if marker.exists():
            ids.add(int(marker.read_text(encoding="utf-8").strip()))
        if ids:
            marks = ",".join("?" * len(ids))
            con.execute(f"DELETE FROM agent_events WHERE run_id IN ({marks})", sorted(ids))
            con.execute(f"DELETE FROM agent_runs WHERE id IN ({marks})", sorted(ids))
            con.commit()
            log(f"刪掉上次中途停掉的 AI 工作：{sorted(ids)}")
    finally:
        con.close()
    marker.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="產生示範資料")
    parser.add_argument("--data-dir", type=Path, required=True, help="資料目錄，例如 data-demo-build")
    parser.add_argument("--model", help="模型名稱，預設用 SAgent 設定裡的模型")
    parser.add_argument("--fake", action="store_true", help="用假模型，只檢查流程")
    parser.add_argument(
        "--script", type=Path, help="用腳本模型回答這個目錄的內容檔，例如 scripts/demo_content"
    )
    args = parser.parse_args()
    if args.fake and args.script:
        parser.error("--fake 和 --script 只能選一個")

    data_dir = args.data_dir.resolve()
    if data_dir == (ROOT_DIR / "data").resolve():
        print("不能寫進正式資料目錄 data/", file=sys.stderr)
        return 2
    settings = sagent_settings()
    model = args.model or settings["default_model"]
    (data_dir / "templates").mkdir(parents=True, exist_ok=True)
    (data_dir / "templates" / "test_plan.yaml").write_text(TEST_PLAN_TEMPLATE, encoding="utf-8")
    cfg = load_config(
        data_dir=data_dir,
        db_path=data_dir / "sagent.db",
        llm_provider="fake" if args.fake else "ollama",
        fake_delay=0.01 if args.fake else 0.0,
        ollama_url=settings["ollama_url"],
        default_model=model,
        allowed_hosts=["testserver"],
    )
    book = None
    if args.script:
        book = ContentBook(args.script.resolve())
        keys = [(sc, key) for sc in SCENARIOS for key in [*SCRIPT_KEYS.values(), "revise"]]
        keys += [(sc, key) for sc in ALGO_SCENARIOS for key in [*sc.doc_types, "revise"]]
        missing = [
            str(book.path(sc.code, key))
            for sc, key in keys
            if (key != "test_plan" or sc.showcase) and not book.path(sc.code, key).exists()
        ]
        if missing:
            print("缺少內容檔：\n" + "\n".join(missing), file=sys.stderr)
            return 2
    name = "假模型" if args.fake else f"{model}（腳本模型）" if book else model
    log(f"資料目錄：{data_dir}，模型：{name}，設定：{settings}")
    marker = data_dir / "seed-running.txt"
    clean_interrupted(cfg.db_path, marker)
    app = create_app(cfg, llm_factory=book.factory if book else None)
    with TestClient(app, headers={"X-SAgent-Client": "seed-demo"}) as client:
        seed(Seeder(client, model, args.fake, marker, book), settings)
    if book and book.anomalies:
        log("腳本模型遇到不應該發生的呼叫：\n" + "\n".join(book.anomalies))
    log("全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
