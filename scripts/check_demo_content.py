"""檢查示範資料的內容檔（格式見 DEMO_DATA_PLAN.md 第 3 節）。

檢查項目：
- JSON 格式、欄位齊全，sections 數量對得上 plan.sections，圖數對得上大綱
- 每張 Mermaid 圖用前端同版本的 Mermaid 檢查（frontend/scripts/mermaid-validate.mjs）
- 沒有簡體字、圖片語法、「（註：…）」內部說明、<br>
- review.issues 只有 low
- 用腳本模型實際跑一次 AI 流程（不存資料庫）：大綱不能改用範本、不能有警告、
  審查不能有 high/medium 錯誤（例如表格欄位數不對、引用沒定義的編號）、修訂檔的章節標題都要找得到

用法（在專案根目錄）：
    uv run --project backend python scripts/check_demo_content.py scripts/demo_content
    uv run --project backend python scripts/check_demo_content.py scripts/demo_content --only FIX --keys idea,feature
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from demo_llm import ContentBook, ScriptedLLM
from demo_scenarios import ALGO_SCENARIOS, SCENARIOS, AlgoScenario, Scenario
from seed_demo import RUN_OPTIONS, TEST_PLAN_TEMPLATE

from sagent.agents.base import ListEmitter
from sagent.agents.doc_templates import DocTemplate, load_templates
from sagent.agents.pipeline import SpecPipeline
from sagent.agents.types import PipelineOptions, RunInput
from sagent.agents.zh import to_traditional
from sagent.config import ROOT_DIR
from sagent.services.diagrams import normalize_diagram
from sagent.services.markdown_doc import normalize_heading, parse_markdown, strip_heading_number

# key → 文件類型
DOC_TYPES = {
    "idea": "idea_proposal",
    "feature": "functional_spec",
    "use_case": "use_case",
    "uiux": "uiux_advice",
    "sdd": "sdd_five",
    "architecture": "architecture",
    "database": "database",
    "api": "api_spec",
    "change": "change_request",
    "test_plan": "test_plan",
}
# key → Spec 所在階段（只影響提示詞裡的文件資訊）
PHASES = {
    "idea": "需求萃取",
    "feature": "需求萃取",
    "use_case": "需求萃取",
    "uiux": "需求萃取",
    "sdd": "系統設計",
    "architecture": "系統分析",
    "database": "系統分析",
    "api": "系統設計",
    "change": "第二期",
    "test_plan": "驗收測試",
}

_BAD_TEXT = [
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), "有圖片語法 ![]()"),
    (re.compile(r"[（(]註[:：]"), "有「（註：…）」內部說明"),
    (re.compile(r"<br\s*/?>", re.I), "有 <br>"),
    (re.compile(r"```\s*(mermaid|plantuml)"), "內文自己放了圖（圖要放在 diagrams）"),
]


def keys_of(sc: Scenario | AlgoScenario) -> list[str]:
    if isinstance(sc, AlgoScenario):
        return [*sc.doc_types, "revise"]
    keys = ["idea", "feature", "use_case", "uiux", "sdd", "architecture", "database", "api", "change"]
    if sc.showcase:
        keys.append("test_plan")
    return [*keys, "revise"]


def doc_type_of(sc: Scenario | AlgoScenario, key: str) -> str:
    return sc.doc_types[key] if isinstance(sc, AlgoScenario) else DOC_TYPES[key]


def phase_of(sc: Scenario | AlgoScenario, key: str) -> str:
    return sc.phases[0][0] if isinstance(sc, AlgoScenario) else PHASES[key]


def run_options(sc: Scenario | AlgoScenario, doc_type: str) -> dict:
    return {**RUN_OPTIONS.get(doc_type, {}), **sc.options.get(doc_type, {})}


def templates() -> dict[str, DocTemplate]:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "test_plan.yaml").write_text(TEST_PLAN_TEMPLATE, encoding="utf-8")
        return load_templates(Path(tmp))


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


class Checker:
    def __init__(self, root: Path):
        self.root = root
        self.problems: dict[str, list[str]] = {}
        self.mermaid: list[tuple[str, str]] = []  # （檔案, 程式碼）
        self.tpls = templates()

    def bad(self, name: str, text: str) -> None:
        self.problems.setdefault(name, []).append(text)

    # --- 每個檔案自己的檢查 ---

    def check_common(self, name: str, data: dict) -> None:
        for text in strings(data):
            if to_traditional(text) != text:
                diff = [a for a, b in zip(text, to_traditional(text), strict=False) if a != b]
                self.bad(name, f"有簡體字：{''.join(dict.fromkeys(diff))}")
                break
        review = data.get("review")
        if not isinstance(review, dict):
            self.bad(name, "缺少 review")
            return
        for issue in review.get("issues", []):
            if issue.get("severity") != "low":
                self.bad(name, f"review.issues 只能放 low：{issue.get('problem', '')[:40]}")
        points = review.get("review_points", [])
        if not 2 <= len(points) <= 5:
            self.bad(name, f"review.review_points 要 2～5 項，現在 {len(points)} 項")
        if not str(review.get("summary", "")).strip():
            self.bad(name, "review.summary 是空的")
        for item in [*review.get("issues", []), *points]:
            if not re.fullmatch(r"S\d+", str(item.get("section_id", ""))):
                self.bad(name, f"review 的 section_id 要寫 S1、S2…：{item.get('section_id')}")

    def check_bodies(self, name: str, bodies: dict[str, str], *, keep_diagrams: bool = False) -> None:
        """keep_diagrams：修訂的 updates 要保留原本的圖，可以有 ```mermaid。"""
        for label, body in bodies.items():
            if not isinstance(body, str) or not body.strip():
                self.bad(name, f"{label} 是空的")
                continue
            for pattern, problem in _BAD_TEXT[:-1] if keep_diagrams else _BAD_TEXT:
                if pattern.search(body):
                    self.bad(name, f"{label} {problem}")
            first = body.strip().splitlines()[0]
            if re.match(r"^#{1,2}\s", first):
                self.bad(name, f"{label} 開頭不要寫章節標題，小標題用 ###")

    def check_diagram_code(self, name: str, label: str, code: str) -> None:
        if code.strip().startswith("```"):
            self.bad(name, f"{label} 不要包 ```")
            return
        _fixed, errors = normalize_diagram("mermaid", code)
        if errors:
            self.bad(name, f"{label} 語法問題：{'；'.join(errors[:3])}")
        self.mermaid.append((f"{name} {label}", code))

    def check_draft(self, sc: Scenario | AlgoScenario, key: str, data: dict) -> None:
        name = f"{sc.code}/{key}"
        doc_type = doc_type_of(sc, key)
        tpl = self.tpls[doc_type]
        plan = data.get("plan")
        if not isinstance(plan, dict):
            self.bad(name, "缺少 plan")
            return
        for field in ("title", "summary", "sections", "open_questions"):
            if field not in plan:
                self.bad(name, f"plan 缺少 {field}")
        secs = plan.get("sections", [])
        if not 2 <= len(secs) <= 10:
            self.bad(name, f"章節要 2～10 節，現在 {len(secs)} 節")
        planned = 0
        for i, sec in enumerate(secs, start=1):
            for field in ("heading", "goal", "key_points", "diagrams"):
                if field not in sec:
                    self.bad(name, f"plan.sections 第 {i} 節缺少 {field}")
            if re.match(r"^\d", str(sec.get("heading", ""))):
                self.bad(name, f"第 {i} 節標題不要加編號：{sec.get('heading')}")
            if len(sec.get("diagrams", [])) > 2:
                self.bad(name, f"第 {i} 節超過 2 張圖")
            planned += len(sec.get("diagrams", []))
        bodies = data.get("sections", [])
        if len(bodies) != len(secs):
            self.bad(name, f"sections 有 {len(bodies)} 段，大綱有 {len(secs)} 節")
        self.check_bodies(name, {f"sections[{i}]": b for i, b in enumerate(bodies)})
        codes = data.get("diagrams", [])
        limit = run_options(sc, doc_type).get("max_diagrams", 4) if tpl.allow_diagrams else 0
        if tpl.max_diagrams is not None:
            limit = min(limit, tpl.max_diagrams)
        if planned > limit:
            self.bad(name, f"大綱有 {planned} 張圖，這種文件最多 {limit} 張")
        if len(codes) != planned:
            self.bad(name, f"diagrams 有 {len(codes)} 張，大綱有 {planned} 張")
        for i, code in enumerate(codes):
            self.check_diagram_code(name, f"diagrams[{i}]", code)
        self.check_common(name, data)

    def check_revise(self, sc: Scenario | AlgoScenario, data: dict, target: dict | None) -> None:
        name = f"{sc.code}/revise"
        plan = data.get("plan")
        if not isinstance(plan, dict):
            self.bad(name, "缺少 plan")
            return
        headings = {}
        if target:
            headings = {normalize_heading(s["heading"]): s["heading"] for s in target["plan"]["sections"]}
        updates = {normalize_heading(k): v for k, v in (data.get("updates") or {}).items()}
        news = {normalize_heading(k): v for k, v in (data.get("new_sections") or {}).items()}
        changed = set()
        for change in plan.get("changes", []):
            key = normalize_heading(change.get("heading", ""))
            changed.add(key)
            if headings and key not in headings:
                self.bad(name, f"changes 的「{change.get('heading')}」不是原文件的章節")
            if change.get("action") == "update" and key not in updates:
                self.bad(name, f"updates 缺少「{change.get('heading')}」")
            if change.get("action") not in ("update", "remove"):
                self.bad(name, f"action 只能是 update 或 remove：{change.get('action')}")
        for key in updates:
            if key not in changed:
                self.bad(name, f"updates 的「{key}」沒有列在 changes")
        planned = 0
        for sec in plan.get("new_sections", []):
            key = normalize_heading(sec.get("heading", ""))
            if key not in news:
                self.bad(name, f"new_sections 缺少「{sec.get('heading')}」的內文")
            after = str(sec.get("after", ""))
            if headings and after.upper() != "END" and normalize_heading(after) not in headings:
                self.bad(name, f"new_sections 的 after「{after}」不是原文件的章節")
            planned += len(sec.get("diagrams", []))
        codes = data.get("diagrams", [])
        if len(codes) != planned:
            self.bad(name, f"diagrams 有 {len(codes)} 張，新章節的大綱有 {planned} 張")
        for i, code in enumerate(codes):
            self.check_diagram_code(name, f"diagrams[{i}]", code)
        self.check_bodies(name, {f"updates[{k}]": v for k, v in updates.items()}, keep_diagrams=True)
        self.check_bodies(name, {f"new_sections[{k}]": v for k, v in news.items()})
        # updates 的圖要保留：原本那一節有幾張圖，修改後也要有幾張
        if target:
            idx = {normalize_heading(s["heading"]): i for i, s in enumerate(target["plan"]["sections"])}
            for key, body in updates.items():
                if key in idx:
                    before = sum(
                        len(s["diagrams"]) for s in target["plan"]["sections"][idx[key] : idx[key] + 1]
                    )
                    after = len(re.findall(r"^```mermaid", body, re.M))
                    if after < before:
                        self.bad(name, f"updates「{key}」原本有 {before} 張圖，修改後只有 {after} 張")
                    for code in re.findall(r"```mermaid\n(.*?)\n```", body, re.S):
                        self.check_diagram_code(name, f"updates[{key}] 的圖", code)
        self.check_common(name, data)

    # --- Mermaid ---

    def validate_mermaid(self) -> None:
        if not self.mermaid:
            return
        frontend = ROOT_DIR / "frontend"
        proc = subprocess.run(
            ["node", "scripts/mermaid-validate.mjs"],
            input=json.dumps([code for _, code in self.mermaid], ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=frontend,
            shell=sys.platform == "win32",
        )
        if proc.returncode != 0:
            raise RuntimeError(f"mermaid-validate.mjs 執行失敗：{proc.stderr[:500]}")
        for (label, _), result in zip(self.mermaid, json.loads(proc.stdout), strict=True):
            if not result["ok"]:
                name, _, where = label.partition(" ")
                self.bad(name, f"{where} Mermaid 檢查失敗：{result['error'][:200]}")

    # --- 用腳本模型實際跑一次 ---

    async def dry_run(
        self, sc: Scenario | AlgoScenario, key: str, book: ContentBook, existing: str = ""
    ) -> str:
        name = f"{sc.code}/{key}"
        target_key = sc.revise_target if key == "revise" else key
        doc_type = doc_type_of(sc, target_key)
        tpl = self.tpls[doc_type]
        opts = run_options(sc, doc_type)
        inp = RunInput(
            mode="revise" if key == "revise" else "draft",
            project_name=sc.name,
            project_description=sc.description,
            phase_name=phase_of(sc, target_key),
            spec_title=sc.titles[target_key],
            template=tpl,
            requirement=sc.texts.get(key) or sc.texts.get("feature_extra", "需求"),
            existing_content=existing,
            options=PipelineOptions(
                uml_syntax=opts.get("uml_syntax", "auto"),
                max_revisions=opts.get("max_revisions", 1),
                diagrams=tpl.allow_diagrams,
                max_diagrams=opts.get("max_diagrams", 4),
            ),
        )
        emitter = ListEmitter()
        before = len(book.anomalies)
        try:
            result = await SpecPipeline(
                ScriptedLLM(book, sc.code, key, model="script", delay=0), emitter
            ).run(inp)
        except Exception as exc:  # noqa: BLE001
            self.bad(name, f"跑 AI 流程失敗：{exc}")
            return ""
        for note in book.anomalies[before:]:
            self.bad(name, f"跑 AI 流程時：{note}")
        if result.plan.fallback:
            self.bad(name, "大綱沒有被採用，改用了範本章節")
        for ev in emitter.events:
            if ev["type"] == "log" and ev["data"].get("level") == "warn":
                self.bad(name, f"跑 AI 流程時有警告：{ev['message']}")
        for issue in result.review.issues if result.review else []:
            if issue.severity in ("high", "medium"):
                self.bad(name, f"審查錯誤 [{issue.section_id}] {issue.problem}")
        drawn = sum(
            1
            for ev in emitter.events
            if ev["type"] == "step_end" and ev["data"]["step"].startswith("diagram:")
        )
        if drawn != len(book.load(sc.code, key).get("diagrams", [])):
            self.bad(
                name,
                f"實際畫了 {drawn} 張圖，diagrams 有 {len(book.load(sc.code, key).get('diagrams', []))} 張",
            )
        return result.markdown

    async def check_project(self, sc: Scenario | AlgoScenario, wanted: set[str] | None) -> None:
        book = ContentBook(self.root)
        outputs: dict[str, str] = {}
        for key in keys_of(sc):
            path = book.path(sc.code, key)
            if not path.exists():
                if wanted is None or key in wanted:
                    self.bad(f"{sc.code}/{key}", f"缺少內容檔 {path}")
                continue
            if wanted is not None and key not in wanted and key != sc.revise_target:
                continue
            try:
                data = book.load(sc.code, key)
            except json.JSONDecodeError as exc:
                self.bad(f"{sc.code}/{key}", f"JSON 格式錯誤：{exc}")
                continue
            if key == "revise":
                target = None
                if book.path(sc.code, sc.revise_target).exists():
                    target = book.load(sc.code, sc.revise_target)
                self.check_revise(sc, data, target)
                if sc.revise_target in outputs:
                    revised = await self.dry_run(sc, key, book, outputs[sc.revise_target])
                    self.check_revised_output(sc, outputs[sc.revise_target], revised)
            else:
                self.check_draft(sc, key, data)
                outputs[key] = await self.dry_run(sc, key, book)

    def check_revised_output(self, sc: Scenario | AlgoScenario, before: str, after: str) -> None:
        if not after:
            return
        old = [strip_heading_number(s.heading) for s in parse_markdown(before).sections]
        new = [strip_heading_number(s.heading) for s in parse_markdown(after).sections]
        if old == new and before.strip() == after.strip():
            self.bad(f"{sc.code}/revise", "修訂後內容沒有變")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="檢查示範資料的內容檔")
    parser.add_argument("root", type=Path, help="內容目錄，例如 scripts/demo_content")
    parser.add_argument("--only", help="只檢查這些專案代碼，逗號分隔，例如 FIX,PUR")
    parser.add_argument("--keys", help="只檢查這些文件，逗號分隔，例如 idea,feature")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None
    wanted = set(args.keys.split(",")) if args.keys else None
    checker = Checker(args.root)
    for sc in [*SCENARIOS, *ALGO_SCENARIOS]:
        if only is None or sc.code in only:
            asyncio.run(checker.check_project(sc, wanted))
    checker.validate_mermaid()

    if not checker.problems:
        print("全部通過")
        return 0
    for name, problems in sorted(checker.problems.items()):
        print(f"\n{name}")
        for p in dict.fromkeys(problems):
            print(f"  - {p}")
    print(f"\n有問題的檔案：{len(checker.problems)} 個")
    return 1


if __name__ == "__main__":
    sys.exit(main())
