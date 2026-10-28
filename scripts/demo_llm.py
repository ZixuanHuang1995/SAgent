"""腳本模型：把子代理事先寫好的內容檔，依序回答給 SAgent 真正的 AI 流程（Planner → Writer → Reviewer）。

內容檔放在 <內容目錄>/<專案代碼>/<key>.json，格式見 DEMO_DATA_PLAN.md 第 3 節。
AI 工作紀錄、步驟事件、大綱、審查結果、人工覆核項目、版本都跟真的模型跑出來的一樣，只是幾秒就跑完。

用法：
    book = ContentBook(Path("scripts/demo_content"))
    app = create_app(cfg, llm_factory=book.factory)
    book.current = ("FIX", "feature")   # 每次送出 AI 工作前設定
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from sagent.agents.llm import LLMError, LLMResult, TokenCallback
from sagent.services.markdown_doc import normalize_heading

# 刻意讓 AI 工作失敗用的模型名稱（seed_demo.py 也用這份清單）
MISSING_MODELS = ("llama3.1:8b", "gemma3:12b", "mistral:7b")

EMPTY_REVIEW = {"issues": [], "review_points": [], "summary": "沒有發現格式或邏輯錯誤。"}

# 串流：每次送出的字數、每次之間的停頓。Windows 上 asyncio 的停頓最少約 15 毫秒，片段不要切太細
CHUNK = 40
DELAY = 0.002


def _between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i == -1:
        return ""
    i += len(start)
    j = text.find(end, i)
    return text[i : j if j != -1 else len(text)].strip()


def _line_after(text: str, prefix: str) -> str:
    m = re.search(rf"^{re.escape(prefix)}(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def _key(heading: str) -> str:
    return normalize_heading(heading)


class ContentBook:
    """內容檔的目錄。current 是目前這個 AI 工作對應的（專案代碼, key）。"""

    def __init__(self, root: Path):
        self.root = root
        self.current: tuple[str, str] | None = None
        self.anomalies: list[str] = []  # 不應該發生的呼叫（例如修訂、修正圖），記下來最後報告
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}

    def path(self, code: str, key: str) -> Path:
        return self.root / code / f"{key}.json"

    def load(self, code: str, key: str) -> dict[str, Any]:
        if (code, key) not in self._cache:
            path = self.path(code, key)
            if not path.exists():
                raise FileNotFoundError(f"缺少內容檔：{path}")
            self._cache[(code, key)] = json.loads(path.read_text(encoding="utf-8"))
        return self._cache[(code, key)]

    def factory(self, model: str, opts: dict[str, Any]) -> ScriptedLLM:
        if self.current is None:
            raise RuntimeError("腳本模型沒有設定目前的內容檔（ContentBook.current）")
        return ScriptedLLM(self, *self.current, model=model)


class ScriptedLLM:
    def __init__(self, book: ContentBook, code: str, key: str, *, model: str, delay: float = DELAY):
        self.book = book
        self.code = code
        self.key = key
        self.model = model
        self.delay = delay
        self.content = book.load(code, key)
        self.revise = "updates" in self.content
        self._used_diagrams: set[int] = set()
        self._last_diagram = ""

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
        if self.model in MISSING_MODELS:
            # 跟 OllamaClient 找不到模型時一樣的錯誤
            raise LLMError(f"Ollama 沒有模型 {self.model}，請先執行 ollama pull {self.model}")
        started = time.monotonic()
        prompt = messages[-1]["content"]
        full = "\n".join(m["content"] for m in messages)
        text = self._respond(purpose, prompt, full)
        for i in range(0, len(text), CHUNK):
            if on_token:
                on_token(text[i : i + CHUNK])
            if self.delay:
                await asyncio.sleep(self.delay)
        return LLMResult(
            text=text,
            prompt_tokens=len(full) // 2,
            completion_tokens=len(text) // 2,
            duration_s=time.monotonic() - started,
        )

    def _anomaly(self, purpose: str, note: str) -> None:
        self.book.anomalies.append(f"{self.code}/{self.key} {purpose}：{note}")

    # ------------------------------------------------------------------

    def _respond(self, purpose: str, prompt: str, full: str) -> str:
        role, _, step = purpose.partition(":")
        if role == "planner":
            plan = self._revise_plan(full) if self.revise else self.content["plan"]
            return json.dumps(plan, ensure_ascii=False)
        if role == "reviewer":
            review = self.content.get("review") or EMPTY_REVIEW
            return json.dumps(review if step == "review:1" else EMPTY_REVIEW, ensure_ascii=False)
        if step.startswith("diagram"):
            return self._diagram(purpose, prompt)
        if step.startswith("fixdiagram"):
            self._anomaly(purpose, "Reviewer 要求修正圖，原樣回傳")
            return _between(full, "## 有語法錯誤的圖\n", "\n## ") or "```mermaid\n```"
        if step.startswith("revise"):
            self._anomaly(purpose, "Reviewer 要求修訂章節，原樣回傳")
            return _between(full, "（目前內容開始）", "（目前內容結束）")
        if step.startswith("section"):
            return self._section(purpose, prompt)
        raise LLMError(f"腳本模型不認得這個呼叫：{purpose}")

    def _revise_plan(self, full: str) -> dict[str, Any]:
        """內容檔用章節標題，換成提示詞裡的 E1、E2… 編號。"""
        ids = {_key(heading): sid for sid, heading in re.findall(r"^\[(E\d+)\] (.+)$", full, re.M)}
        plan = json.loads(json.dumps(self.content["plan"]))
        for change in plan.get("changes", []):
            heading = change.pop("heading", "")
            if _key(heading) not in ids:
                raise LLMError(f"{self.code}/{self.key}：原文件找不到要修改的章節「{heading}」")
            change["section_id"] = ids[_key(heading)]
        for sec in plan.get("new_sections", []):
            after = str(sec.get("after", "END")).strip()
            if after.upper() == "END":
                sec["after"] = "END"
            elif _key(after) in ids:
                sec["after"] = ids[_key(after)]
            else:
                raise LLMError(f"{self.code}/{self.key}：原文件找不到「{after}」，新章節不知道放哪裡")
        return plan

    def _section(self, purpose: str, prompt: str) -> str:
        if "（原本內容開始）" in prompt:
            heading = _line_after(prompt, "## 要修改的章節：")
            return self._lookup(self.content.get("updates", {}), heading, purpose)
        heading = _first_section_line(prompt)
        if self.revise:
            return self._lookup(self.content.get("new_sections", {}), heading, purpose)
        for i, sec in enumerate(self.content["plan"]["sections"]):
            if _key(sec["heading"]) == _key(heading):
                return self.content["sections"][i]
        raise LLMError(f"{self.code}/{self.key}：內容檔沒有「{heading}」這一節（{purpose}）")

    def _lookup(self, table: dict[str, str], heading: str, purpose: str) -> str:
        for name, body in table.items():
            if _key(name) == _key(heading):
                return body
        raise LLMError(f"{self.code}/{self.key}：內容檔沒有「{heading}」的內文（{purpose}）")

    def _planned_diagrams(self) -> list[dict[str, Any]]:
        sections = self.content["plan"].get("new_sections" if self.revise else "sections", [])
        return [d for sec in sections for d in sec.get("diagrams", [])]

    def _diagram(self, purpose: str, prompt: str) -> str:
        if prompt.startswith("這段程式碼有語法錯誤"):
            self._anomaly(purpose, "圖的語法有錯，Writer 要求重畫，原樣回傳")
            return self._last_diagram
        codes = self.content.get("diagrams", [])
        title = _line_after(_between(prompt, "## 要畫的圖", "## 語法"), "標題：")
        planned = self._planned_diagrams()
        index = next(
            (
                i
                for i, d in enumerate(planned)
                if i not in self._used_diagrams and str(d.get("title", "")).strip()[:60] == title
            ),
            None,
        )
        if index is None:
            index = next((i for i in range(len(codes)) if i not in self._used_diagrams), None)
            self._anomaly(purpose, f"大綱裡找不到圖「{title}」，改用順序")
        if index is None or index >= len(codes):
            raise LLMError(f"{self.code}/{self.key}：內容檔沒有「{title}」這張圖（{purpose}）")
        self._used_diagrams.add(index)
        self._last_diagram = f"```mermaid\n{codes[index].strip()}\n```"
        return self._last_diagram


def _first_section_line(prompt: str) -> str:
    """撰寫章節的提示詞裡「第 3 節：標題」那一行。"""
    m = re.search(r"^第 \d+ 節：(.+)$", prompt.split("## 現在要寫的章節")[-1], re.M)
    return m.group(1).strip() if m else ""
