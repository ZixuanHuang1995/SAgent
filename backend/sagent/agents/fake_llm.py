"""假的 LLM：不接模型，依呼叫目的回傳固定格式的內容。

用途：自動測試、沒有模型時展示畫面流程（設定 SAGENT_LLM_PROVIDER=fake）。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from .diagram_kinds import KINDS
from .llm import LLMResult, TokenCallback


def _between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i == -1:
        return ""
    i += len(start)
    j = text.find(end, i)
    return text[i : j if j != -1 else len(text)].strip()


def _section(text: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


class FakeLLM:
    def __init__(
        self, model: str = "fake", delay: float = 0.0, fail_json: bool = False, call_delay: float = 0.0
    ):
        self.model = model
        self.delay = delay  # 每個片段之間的停頓（模擬串流）
        self.call_delay = call_delay  # 每次呼叫開始前的停頓（模擬模型讀提示詞）
        self.fail_json = fail_json
        self.calls: list[dict[str, Any]] = []

    async def close(self) -> None:
        return None

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_token: TokenCallback | None = None,
        purpose: str = "",
    ) -> LLMResult:
        started = time.monotonic()
        if self.call_delay:
            await asyncio.sleep(self.call_delay)
        prompt = messages[-1]["content"]
        full = "\n".join(m["content"] for m in messages)
        self.calls.append({"purpose": purpose, "schema": schema is not None, "messages": messages})
        text = self._respond(purpose, prompt, full, schema)
        for i in range(0, len(text), 4):
            if on_token:
                on_token(text[i : i + 4])
            if self.delay:
                await asyncio.sleep(self.delay)
        return LLMResult(
            text=text,
            prompt_tokens=len(full) // 2,
            completion_tokens=len(text) // 2,
            duration_s=time.monotonic() - started,
        )

    # ------------------------------------------------------------------

    def _respond(self, purpose: str, prompt: str, full: str, schema: dict[str, Any] | None) -> str:
        role, _, step = purpose.partition(":")
        if role == "planner":
            if self.fail_json:
                return "抱歉，我無法輸出 JSON。"
            if schema and "changes" in schema.get("properties", {}):
                return self._revise_plan(full)
            return self._plan(full)
        if role == "reviewer":
            if self.fail_json:
                return "審查完成，沒有問題。"
            return self._review(step)
        if step.startswith(("diagram", "fixdiagram")):
            return self._diagram(full)
        if step.startswith("revise"):
            current = _between(full, "（目前內容開始）", "（目前內容結束）")
            return current + "\n\n- 補充：發生例外時要記錄錯誤並通知使用者（假設）。"
        if "（原本內容開始）" in full:
            original = _between(full, "（原本內容開始）", "（原本內容結束）")
            instruction = _section(full, "修改說明").splitlines()[0]
            return f"{original}\n\n- 修訂：{instruction}"
        return self._section_text(
            prompt,
            advice="這是建議文件" in full,
            idea="這是構想文件" in full,
            algorithm="這是演算法設計文件" in full,
        )

    def _plan(self, full: str) -> str:
        sections = []
        for line in _section(full, "建議章節").splitlines():
            m = re.match(r"^- (.+?)：(.*?)（建議的圖：(.*)）$", line.strip())
            if not m:
                continue
            heading, goal, diagrams = m.groups()
            kinds = [d.split(" ")[0] for d in diagrams.split("、") if d.split(" ")[0] in KINDS]
            sections.append(
                {
                    "heading": heading,
                    "goal": goal,
                    "key_points": [f"{heading}的重點一", f"{heading}的重點二", f"{heading}的重點三"],
                    "diagrams": [
                        {"kind": k, "title": f"{heading}{KINDS[k].name}", "description": goal} for k in kinds
                    ],
                }
            )
        plan = {
            "title": "示範文件",
            "summary": "這是假模型產生的示範文件，用來檢查流程是否正常。",
            "sections": sections[:6],
            "open_questions": ["需求中的使用者人數尚未確認", "是否需要支援行動裝置"],
        }
        return json.dumps(plan, ensure_ascii=False, indent=2)

    def _revise_plan(self, full: str) -> str:
        ids = re.findall(r"^\[(E\d+)\] (.+)$", full, re.M)
        changes = []
        if ids:
            changes.append(
                {"section_id": ids[0][0], "action": "update", "instruction": "加入修訂需求提到的內容"}
            )
        plan = {
            "summary": "依修訂需求補充內容",
            "changes": changes,
            "new_sections": [
                {
                    "after": "END",
                    "heading": "修訂補充",
                    "goal": "說明這次新增的需求",
                    "key_points": ["新增需求的範圍"],
                    "diagrams": [],
                }
            ],
            "open_questions": ["新需求的上線時間"],
        }
        return json.dumps(plan, ensure_ascii=False)

    def _section_text(
        self, prompt: str, advice: bool = False, idea: bool = False, algorithm: bool = False
    ) -> str:
        m = re.search(r"第 (\d+) 節：(.+)", prompt)
        heading = m.group(2).strip() if m else "本節"
        number = int(m.group(1)) if m else 1
        points = re.findall(r"^- (.+)$", _between(prompt, "要點：", "請輸出"), re.M)
        bullet = "\n".join(f"- {p}" for p in points[:4]) or "- 依需求內容撰寫"
        # 「編號：」那一行指定了格式就照著用，沒有指定就不編號
        prefix = re.search(r"^編號：這一節的項目用 ([A-Z]{1,4})-01", prompt, re.M)
        if idea:
            # 構想文件不寫細節規格，只整理想法；使用者故事、驗收條件照指定的格式編號
            items = f"- {prefix.group(1)}-01：{heading}的第一項\n" if prefix else ""
            return f"{heading}說明如下。\n\n{items}{bullet}\n- 細節還要跟需求提出人確認（待確認）。\n"
        if algorithm and "```text" in _between(prompt, "目標：", "\n"):
            # 演算法設計文件的虛擬碼章節
            code = "function solve(input)\n  for each item in input\n    if item is valid\n      return item\n  return none"
            return f"{heading}如下。\n\n```text\n{code}\n```\n\n{bullet}\n"
        item_id = f"{prefix.group(1)}-{number:02d}" if prefix else "—"
        text = f"建議{heading}保持簡單，常用操作放在明顯的位置" if advice else f"系統要提供{heading}相關功能"
        return (
            f"{heading}說明如下。\n\n{bullet}\n\n"
            f"| 編號 | 項目 | 說明 |\n|---|---|---|\n"
            f"| {item_id} | {heading} | {text} |\n"
        )

    def _diagram(self, full: str) -> str:
        lang = "plantuml" if "## 語法：PlantUML" in full else "mermaid"
        kind = next((k for k in KINDS.values() if f"類型：{k.name}" in full), KINDS["flowchart"])
        if "有語法錯誤的圖" in full:
            code = re.search(r"## 有語法錯誤的圖\n```(\w+)\n(.*?)\n```", full, re.S)
            if code:
                lang = "plantuml" if code.group(1) == "plantuml" else "mermaid"
                if "erDiagram" in code.group(2):
                    kind = KINDS["er"]
        example = kind.plantuml_example if lang == "plantuml" else kind.mermaid_example
        return f"```{lang}\n{example}\n```"

    def _review(self, step: str) -> str:
        round_no = int(re.sub(r"\D", "", step) or 1)
        if round_no == 1:
            data = {
                "issues": [
                    {
                        "section_id": "S2",
                        "kind": "conflict",
                        "severity": "medium",
                        "problem": "第 2 節沒有寫發生例外時的處理，跟其他章節的流程接不起來",
                        "suggestion": "補上例外情況和處理方式",
                    }
                ],
                "review_points": [
                    {"section_id": "S1", "item": "需求沒有提到使用者人數", "reason": "要跟需求提出人確認規模"}
                ],
                "summary": "第 2 節的流程有缺口，另有 1 項要人工確認。",
            }
        else:
            data = {"issues": [], "review_points": [], "summary": "沒有發現格式或邏輯錯誤。"}
        return json.dumps(data, ensure_ascii=False)
