"""產生示範資料：一個完整的「設備報修系統」專案，AI 撰寫的部分用真的地端模型。

涵蓋的功能：
- 四種類型、九種文件類型都各有一份，其中八份由 AI 撰寫，一份是範本骨架
- 構想 → 轉成功能規格（來源追溯）
- 依需求修訂（AI 修訂模式）、手動編輯、版本標籤、還原
- 各種狀態（草稿、審核中、已核准、已停用）、封存、匯入 Markdown
- 兩個基準版本，可以比較前後差異
- 第二個專案，讓專案清單不會只有一筆

整個流程透過 API 進行（在程式內啟動 app，不用另外開伺服器），AI 工作紀錄、步驟事件都會存進資料庫，
跟使用者在網頁上操作的結果一樣。

可以重複執行：已經做過的步驟會跳過，中途失敗後再跑一次會從失敗的地方繼續。

用法（在專案根目錄）：
    uv run --project backend python scripts/seed_demo.py --data-dir data-demo-build
    uv run --project backend python scripts/seed_demo.py --data-dir data-demo-build --model qwen3:8b
    uv run --project backend python scripts/seed_demo.py --data-dir 暫存目錄 --fake   # 用假模型檢查腳本

CPU 上用 qwen3.6:35b-a3b 全部跑完大約要 7～8 小時（一份文件 30～80 分鐘）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from sagent.config import ROOT_DIR, load_config
from sagent.main import create_app

STARTED = time.monotonic()


def log(text: str) -> None:
    elapsed = time.monotonic() - STARTED
    print(f"[{int(elapsed // 3600):d}:{int(elapsed % 3600 // 60):02d}:{int(elapsed % 60):02d}] {text}", flush=True)


# ---------------------------------------------------------------------------
# 情境：設備報修系統
# ---------------------------------------------------------------------------

PROJECT = {
    "name": "設備報修系統",
    "code": "FIX",
    "description": "公司內部辦公設備和大樓設施的線上報修、派工、追蹤平台，取代目前的紙本報修單和電話報修。",
    "phases": ["需求分析", "系統設計", "詳細設計", "第二期"],
}

IDEA_REQ = """目前員工發現設備故障（冷氣、電腦、印表機、門禁），要填紙本報修單或打電話給總務。常常找不到人，也不知道修到哪裡了。總務每個月還要把報修單一張張抄到 Excel 做統計。

想做一個手機和電腦都能用的線上報修：員工拍照、選地點就能報修；維修人員在手機上收到工單，處理完直接回報；主管可以看各類故障的數量和處理時間。

可能先在總部大樓試辦，之後再推到廠區。還不確定要自己開發，還是買現成的工單系統。"""

FEATURE_EXTRA = """

---

需求訪談補充（2026/10/15，總務部、資訊部）：
1. 角色：報修人（全體員工，用公司 SSO 登入）、總務窗口（負責派工）、維修人員（內部技師或外包廠商）、部門主管（看報表）。
2. 報修單內容：設備類別、地點（大樓、樓層、區域）、問題說明、照片最多 3 張、緊急程度（一般、緊急）。
3. 派工：總務窗口依設備類別指派維修人員。緊急報修要在 1 小時內派工。
4. 報修單狀態：已提交 → 已派工 → 處理中 → 待確認 → 已結案。總務窗口可以退回資料不完整的報修單，報修人可以取消。
5. 維修人員回報處理結果和使用的材料，報修人確認後結案；3 天沒有確認就自動結案。
6. 每次狀態改變都要通知報修人（Email 和 Teams）。
7. 報表：每月各設備類別的報修數量、平均處理時間、逾期件數，可以匯出 Excel。
8. 第一期只做總部大樓，大約 200 位員工使用。決定自己開發，不買現成系統。"""

USE_CASE_REQ = """維修人員處理工單的流程（總務部、維修組訪談整理）：
1. 維修人員用手機登入，看到「指派給我」的工單，緊急的排在最前面。
2. 接單後狀態改成「處理中」，系統記錄接單時間。
3. 到現場發現自己無法處理（例如需要原廠），可以申請轉外包，或退回給總務窗口，都要寫原因。
4. 需要等零件時，把工單標成「等待零件」，這段時間不算進處理時間。零件到了再改回處理中。
5. 處理完填寫處理說明、使用的材料和數量，可以上傳完工照片，送出後狀態變成「待確認」。
6. 報修人確認後結案；如果沒修好可以退回重修。同一張工單退回 2 次後，第 3 次要通知維修組長介入。
7. 總務窗口可以隨時改派給其他維修人員，原本的維修人員會收到通知。"""

UIUX_REQ = """設備報修系統的使用者和使用狀況：
1. 報修人：全體員工，大多用手機在故障現場報修，很多人一年只報修一兩次，不熟悉系統。希望 1 分鐘內完成報修。
2. 維修人員：整天在大樓裡走動，用手機看工單、回報，有時候戴著工作手套，常在訊號不好的機房和地下室。
3. 總務窗口：用電腦，每天要看幾十張報修單並派工，最在意不要漏掉緊急件。
4. 部門主管：每月看一次報表，用電腦。

主要功能：線上報修（設備類別、地點、問題說明、照片、緊急程度）、派工、維修人員接單和回報、報修人確認結案、狀態通知、月報表。"""

SDD_REQ = """報修單的狀態和派工規則（給開發團隊用，AI 輔助開發時會直接參考）：
1. 狀態：已提交、已退回、已派工、處理中、等待零件、待確認、已結案、已取消。
2. 只有總務窗口可以派工、改派、退回；只有被指派的維修人員可以接單和回報；報修人只能在派工前取消。
3. 緊急報修提交後 1 小時內沒有派工，通知總務主管。
4. 待確認超過 3 天（日曆天）自動結案，結案原因記錄「逾期自動結案」。
5. 同一張報修單被報修人退回重修 2 次以上，第 3 次退回時通知維修組長。
6. 欄位規則：問題說明 10～500 字；照片 jpg 或 png，每張最大 5MB，最多 3 張。
7. 下拉選單值：設備類別（空調、電腦週邊、印表機、門禁、水電、其他）；緊急程度（一般、緊急）。
8. 已決定：第一期只做總部大樓；登入用公司 Azure AD SSO；通知用 Email 和 Teams。
9. 限制：要在 2027 年 1 月上線；不能改動現有的門禁系統。"""

ARCH_REQ = """設備報修系統的技術條件：
1. 部署在公司內部機房的 VM，不能用公有雲。
2. 前端是網頁，用 RWD 支援手機，不另外做 App。
3. 後端提供 REST API；資訊部熟悉 C# .NET 8 和 Vue 3。
4. 資料庫用公司現有的 SQL Server 2019。
5. 登入接公司 Azure AD SSO；權限依 AD 群組（員工、總務窗口、維修人員、主管）。
6. 通知：Email 走公司 SMTP 伺服器，Teams 用 Incoming Webhook 發到總務頻道和個人。
7. 照片存在公司檔案伺服器（NAS），資料庫只存路徑。
8. 報表資料每天晚上批次統計一次。
9. 所有派工、狀態變更都要留稽核紀錄，保存 5 年。
10. 使用者約 200 人，尖峰同時上線約 30 人；上班時間可用性要 99%。"""

DB_REQ = """設備報修系統的資料需求：
1. 員工資料每天從 AD 同步：工號、姓名、部門、Email、角色。
2. 地點分三層：大樓、樓層、區域。設備類別可以由總務維護。
3. 報修單：編號規則 R + 年月日 + 4 碼流水號（例如 R202610150001）、報修人、設備類別、地點、問題說明、緊急程度、狀態、建立時間、結案時間。
4. 照片：一張報修單最多 3 張，存檔案路徑、檔名、大小、上傳者。
5. 派工紀錄：每次派工、改派都要留一筆，記錄派給誰、誰派的、時間、原因。
6. 狀態歷程：每次狀態改變記錄前後狀態、操作人、時間、備註。
7. 處理回報：處理說明、使用材料（材料名稱、數量、單位）、完工時間。
8. 通知紀錄：通知對象、管道（Email、Teams）、內容摘要、發送結果。
9. 資料保存 5 年，照片 2 年後移到封存區。刪除一律用軟刪除。
10. 資料庫是 SQL Server 2019。"""

API_REQ = """設備報修系統後端 API 需求：
1. REST API，JSON 格式，Base URL 是 /api/v1。
2. 驗證：前端用 Azure AD 取得 access token，用 Authorization: Bearer 帶入；後端依 AD 群組判斷權限。
3. 報修人：建立報修單、上傳照片（multipart）、查詢自己的報修單、取消（派工前）、確認結案或退回重修。
4. 總務窗口：查詢全部報修單（可依狀態、類別、緊急程度、日期篩選，分頁）、派工、改派、退回。
5. 維修人員：查詢指派給我的工單、接單、標記等待零件、回報處理結果。
6. 主管：月報表查詢、匯出 Excel。
7. 分頁用 page 和 page_size，page_size 最大 100。
8. 時間一律用 ISO 8601（UTC+8）。
9. 狀態不允許的操作回 409，權限不足回 403。"""

REVISE_REQ = """客戶補充（2026/10/20 需求審查會議）：
1. 緊急報修改成 30 分鐘內要派工，超過 30 分鐘自動通知總務主管。
2. 報修人在派工前可以自己取消報修單，要填取消原因。
3. 報表增加「各維修人員處理件數和平均處理時間」。"""

CHANGE_REQ = """第二期變更：設備 QR Code 報修

現況問題：報修人要自己選設備類別和地點，常常選錯；維修人員到現場找不到是哪一台設備。

變更內容：
1. 每台設備貼上 QR Code（內容是設備編號）。用手機掃描後打開報修頁，自動帶入設備編號、設備類別和地點，報修人只要寫問題說明。
2. 新增「設備清冊」功能，由總務窗口維護設備編號、名稱、類別、地點、購買日期、保固到期日，可以批次匯入 Excel 和列印 QR Code 標籤。
3. 報修單新增「設備編號」欄位（選填，舊的報修單沒有這個欄位）。
4. 報表新增「依設備統計」，找出常故障的設備。
5. 不掃 QR Code 的原本報修方式保留。

上線方式：先在總部 3 樓試行一個月，沒問題再全部貼上 QR Code。"""

GLOSSARY_MD = """# 名詞對照表

> 從總務部提供的 Excel 匯入，給所有文件共用。

| 名詞 | 說明 |
|---|---|
| 報修人 | 提出報修的員工 |
| 總務窗口 | 負責審核報修單、派工的總務人員 |
| 維修人員 | 內部技師或外包廠商人員 |
| 維修組長 | 管理內部技師，處理多次退回的工單 |
| 工單 | 已派工的報修單 |
| 結案 | 報修人確認修好，或待確認超過 3 天自動結案 |
| SSO | 單一登入，用公司 Azure AD 帳號登入 |
"""

PAPER_MD = """# 紙本報修流程說明

> 舊流程，線上報修上線後停用。

1. 到總務櫃台領取報修單，填寫設備、地點、問題。
2. 交給總務窗口，總務窗口打電話聯絡維修人員。
3. 維修完成後，維修人員在報修單簽名，交回總務歸檔。
4. 每月底總務人工把報修單登記到 Excel。
"""

OWNER_NOTE = "> 文件負責人：資訊部 系統分析組｜審查：總務部\n"


# ---------------------------------------------------------------------------
# API 小工具
# ---------------------------------------------------------------------------


class Seeder:
    def __init__(self, client: TestClient, model: str):
        self.c = client
        self.model = model

    def call(self, method: str, url: str, **kw):
        resp = self.c.request(method, url, **kw)
        if resp.status_code >= 400:
            raise RuntimeError(f"{method} {url} 失敗：{resp.status_code} {resp.text}")
        return resp.json() if resp.content else None

    # --- 專案與 Spec ---

    def project(self, name: str, code: str, description: str, phases: list[str]) -> tuple[int, dict[str, int]]:
        found = next((p for p in self.call("GET", "/api/projects") if p["name"] == name), None)
        if found is None:
            found = self.call(
                "POST", "/api/projects", json={"name": name, "code": code, "description": description, "phases": phases}
            )
            log(f"建立專案「{name}」")
        pid = found["id"] if "id" in found else found["project"]["id"]
        tree = self.call("GET", f"/api/projects/{pid}")
        return pid, {ph["name"]: ph["id"] for ph in tree["phases"]}

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

    # --- AI 工作 ---

    def run(self, spec: dict, mode: str, requirement: str, retries: int = 1) -> dict:
        for attempt in range(retries + 1):
            run = self.call(
                "POST",
                "/api/agent/runs",
                json={"spec_id": spec["id"], "mode": mode, "requirement": requirement, "options": {"model": self.model}},
            )
            label = "撰寫" if mode == "draft" else "修訂"
            log(f"AI {label}「{spec['title']}」開始（工作 #{run['id']}）")
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
                return run
            steps = [e for e in run["events"] if e["type"] == "step_end"]
            msg = f"{steps[-1]['agent']}：{steps[-1]['message']}" if steps else "等待模型"
            now = time.monotonic()
            if msg != last or now - last_print > 300:
                log(f"  … {msg}")
                last, last_print = msg, now
            time.sleep(5)

    def draft(self, spec: dict, requirement: str) -> dict:
        """AI 撰寫。已經有內容就跳過。"""
        if spec["head_version_no"] > 0:
            return spec
        return self.run(spec, "draft", requirement)

    def revise(self, spec: dict, requirement: str) -> dict:
        """AI 修訂。這份 Spec 已經有成功的修訂工作就跳過。"""
        runs = self.call("GET", "/api/agent/runs", params={"spec_id": spec["id"]})
        if any(r["mode"] == "revise" and r["status"] == "succeeded" for r in runs):
            return spec
        return self.run(spec, "revise", requirement)

    # --- 版本、狀態、基準版本 ---

    def edit(self, spec: dict, marker: str, change, message: str) -> dict:
        """手動編輯：內容裡還沒有 marker 才改。"""
        spec = self.refresh(spec)
        if marker in spec["content"]:
            return spec
        content = change(spec["content"])
        self.call(
            "PUT",
            f"/api/specs/{spec['id']}/content",
            json={"content": content, "message": message, "base_version_no": spec["head_version_no"]},
        )
        log(f"手動編輯「{spec['title']}」：{message}")
        return self.refresh(spec)

    def label(self, spec: dict, label: str, version_no: int | None = None) -> None:
        """在指定版本（預設最新版）加標籤。已經有版本用了這個標籤就跳過，重跑時才不會貼到別的版本。"""
        versions = self.call("GET", f"/api/specs/{spec['id']}/versions")
        if any(v.get("label") == label for v in versions):
            return
        no = version_no or self.refresh(spec)["head_version_no"]
        self.call("PUT", f"/api/specs/{spec['id']}/versions/{no}/label", json={"label": label})

    def status(self, spec: dict, status: str) -> None:
        self.call("PATCH", f"/api/specs/{spec['id']}", json={"status": status})

    def baseline(self, pid: int, name: str, note: str) -> None:
        if any(b["name"] == name for b in self.call("GET", f"/api/projects/{pid}/baselines")):
            return
        self.call("POST", f"/api/projects/{pid}/baselines", json={"name": name, "note": note})
        log(f"建立基準版本「{name}」")


def insert_after_title(text: str, line: str) -> str:
    first, _, rest = text.partition("\n")
    return f"{first}\n\n{line}{rest}"


# ---------------------------------------------------------------------------
# 流程
# ---------------------------------------------------------------------------


def seed(s: Seeder) -> None:
    s.call("PUT", "/api/settings", json={"default_model": s.model})
    pid, ph = s.project(**PROJECT)

    # 1. 構想 → 主管核可
    idea = s.draft(s.spec(pid, ph["需求分析"], "行動報修構想", "idea_proposal"), IDEA_REQ)
    s.label(idea, "主管核可", 1)
    s.status(idea, "approved")

    # 2. 構想轉成功能規格：需求 = 構想內容 + 訪談補充
    feature = s.spec(pid, ph["需求分析"], "報修申請與派工", "functional_spec", source_spec_id=idea["id"])
    feature = s.draft(feature, idea["content"] + FEATURE_EXTRA)
    feature = s.edit(
        feature, "文件負責人", lambda t: insert_after_title(t, OWNER_NOTE), "加入文件負責人和審查單位"
    )
    s.label(feature, "需求審查版")
    s.status(feature, "reviewing")

    # 3. 其他需求分析文件
    use_case = s.draft(s.spec(pid, ph["需求分析"], "維修人員處理工單", "use_case"), USE_CASE_REQ)
    s.status(use_case, "reviewing")
    s.draft(s.spec(pid, ph["需求分析"], "報修畫面與操作建議", "uiux_advice"), UIUX_REQ)
    s.spec(pid, ph["需求分析"], "名詞對照表", "functional_spec", init="content", content=GLOSSARY_MD)
    paper = s.spec(pid, ph["需求分析"], "紙本報修流程說明", "functional_spec", init="content", content=PAPER_MD)
    if not paper["archived"]:
        s.status(paper, "deprecated")
        s.call("POST", f"/api/specs/{paper['id']}/archive")

    s.baseline(pid, "需求確認版", "需求審查會議前的版本組合")

    # 4. 系統設計、詳細設計
    sdd = s.draft(s.spec(pid, ph["詳細設計"], "報修單狀態與派工規則", "sdd_five"), SDD_REQ)
    s.status(sdd, "approved")
    arch = s.draft(s.spec(pid, ph["系統設計"], "報修系統架構", "architecture"), ARCH_REQ)
    s.status(arch, "reviewing")
    db = s.draft(s.spec(pid, ph["系統設計"], "報修系統資料庫", "database"), DB_REQ)
    # 手動加一段，再還原回 AI 的版本（示範還原）
    if db["head_version_no"] == 1:
        db = s.edit(db, "暫存欄位", lambda t: t + "\n## 暫存欄位\n\n- 先保留 remark 欄位（待確認）\n", "加入暫存欄位")
        s.call("POST", f"/api/specs/{db['id']}/versions/1/restore", json={"message": "暫存欄位不需要，還原成 v1"})
        log("還原「報修系統資料庫」到 v1")
    api = s.draft(s.spec(pid, ph["詳細設計"], "報修系統 API", "api_spec"), API_REQ)
    s.spec(pid, ph["詳細設計"], "保固廠商 API 介接", "api_spec", init="template")

    # 5. 依客戶補充修訂功能規格
    feature = s.revise(feature, REVISE_REQ)
    s.label(feature, "客戶確認版")
    s.status(feature, "approved")
    s.status(api, "reviewing")

    s.baseline(pid, "設計審查版", "需求確認後、系統設計審查的版本組合")

    # 6. 第二期變更
    s.draft(s.spec(pid, ph["第二期"], "設備 QR Code 報修", "change_request"), CHANGE_REQ)

    # 第二個專案：只有範本骨架，讓專案清單不只一筆
    pid2, ph2 = s.project(
        "員工請假系統", "LEAVE", "員工線上請假、主管簽核、假勤統計。", ["需求分析", "系統設計", "詳細設計"]
    )
    s.spec(pid2, ph2["需求分析"], "請假申請功能規格", "functional_spec", init="template")


def main() -> int:
    parser = argparse.ArgumentParser(description="產生示範資料")
    parser.add_argument("--data-dir", type=Path, required=True, help="資料目錄，例如 data-demo-build")
    parser.add_argument("--model", default="qwen3.6:35b-a3b")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--fake", action="store_true", help="用假模型，只檢查流程")
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    if data_dir == (ROOT_DIR / "data").resolve():
        print("不能寫進正式資料目錄 data/", file=sys.stderr)
        return 2
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(
        data_dir=data_dir,
        db_path=data_dir / "sagent.db",
        llm_provider="fake" if args.fake else "ollama",
        fake_delay=0.0,
        ollama_url=args.ollama,
        default_model=args.model,
        allowed_hosts=["testserver"],
    )
    log(f"資料目錄：{data_dir}，模型：{'假模型' if args.fake else args.model}")
    with TestClient(create_app(cfg), headers={"X-SAgent-Client": "seed-demo"}) as client:
        seed(Seeder(client, args.model))
    log("全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
