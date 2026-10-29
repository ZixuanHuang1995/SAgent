"""同一份文件請 Reviewer 審查好幾次，看每次找到的錯誤有多少重疊。

用來判斷修訂後的再審查找到的「新錯誤」，是修訂改壞的，還是審查模型每次挑的不一樣。
文件、需求、範本、大綱從某一次 AI 工作讀出來（資料庫只讀，不會修改）。

用法（在專案根目錄）：
    uv run --project backend python scripts/review_consistency.py --db data-demo/sagent.db --run-id 6 --times 3 --out 結果.json
    自訂範本的文件加 --templates-dir（例如 verify_real_model.py 的資料夾裡的 templates）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from sagent.agents.base import NullEmitter
from sagent.agents.doc_templates import get_template, load_templates
from sagent.agents.llm import OllamaClient
from sagent.agents.reviewer import Reviewer
from sagent.agents.types import (
    OPEN_QUESTIONS_HEADING,
    Draft,
    Issue,
    PipelineOptions,
    Plan,
    RunInput,
    SectionDraft,
    SectionPlan,
)
from sagent.services.markdown_doc import normalize_heading, parse_markdown, strip_heading_number

_KEY_RE = re.compile(r"[A-Z]{1,4}-\d{2,3}|「([^」]{2,30})」")


def load_run(db_path: Path, run_id: int, templates_dir: Path | None = None) -> tuple[RunInput, Plan, Draft]:
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    row = db.execute(
        "select r.requirement, r.plan, s.doc_type, s.title, p.name, p.description, ph.name, v.content "
        "from agent_runs r join specs s on s.id = r.spec_id join projects p on p.id = r.project_id "
        "left join phases ph on ph.id = s.phase_id "
        "join spec_versions v on v.spec_id = s.id and v.version_no = r.result_version_no where r.id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"找不到 AI 工作 {run_id}，或它沒有產生版本")
    requirement, plan_json, doc_type, title, project, description, phase, content = row
    template = get_template(load_templates(templates_dir), doc_type)
    planned = {normalize_heading(s["heading"]): s for s in (json.loads(plan_json or "{}").get("sections") or [])}
    sections, drafts = [], []
    for sec in parse_markdown(content).sections:
        heading = strip_heading_number(sec.heading)
        if normalize_heading(heading) == normalize_heading(OPEN_QUESTIONS_HEADING):
            continue
        sid = f"S{len(sections) + 1}"
        p = planned.get(normalize_heading(heading), {})
        sections.append(SectionPlan(id=sid, heading=heading, goal=p.get("goal", ""), ids=p.get("ids", "")))
        drafts.append(SectionDraft(sid, heading, sec.body))
    inp = RunInput(
        mode="draft",
        project_name=project,
        project_description=description or "",
        phase_name=phase or "",
        spec_title=title,
        template=template,
        requirement=requirement,
        options=PipelineOptions(),
    )
    return inp, Plan(title=title, summary="", sections=sections), Draft(drafts)


def _keys(issue: Issue) -> set[str]:
    return {m.group(1) or m.group(0) for m in _KEY_RE.finditer(issue.problem)}


def _bigrams(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text)
    return {text[i : i + 2] for i in range(len(text) - 1)}


def same_issue(a: Issue, b: Issue) -> bool:
    """同一節，且提到同一個編號或引號裡的詞，或文字有三成以上相同，就當成同一個問題。"""
    if a.section_id != b.section_id:
        return False
    if _keys(a) & _keys(b):
        return True
    x, y = _bigrams(a.problem), _bigrams(b.problem)
    return bool(x and y) and len(x & y) / len(x | y) >= 0.3


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--times", type=int, default=3)
    parser.add_argument("--model", default="qwen3.6:35b-a3b")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--out", type=Path, default=Path("review-consistency.json"))
    parser.add_argument("--templates-dir", type=Path, help="自訂範本的資料夾")
    args = parser.parse_args()

    inp, plan, draft = load_run(args.db, args.run_id, args.templates_dir)
    print(f"文件：{inp.spec_title}（{inp.template.name}），{len(plan.sections)} 節", flush=True)
    llm = OllamaClient(args.ollama, args.model)
    results: list[list[Issue]] = []
    raw: list[dict] = []
    try:
        for n in range(1, args.times + 1):
            started = time.monotonic()
            reviewer = Reviewer(llm, NullEmitter(), {})
            review = await reviewer.review(inp, plan, draft, 1)
            errors = [i for i in review.issues if i.source == "llm" and i.severity in ("high", "medium")]
            results.append(errors)
            raw.append(review.to_dict())
            print(
                f"第 {n} 次：{time.monotonic() - started:.0f}s，模型找到錯誤 {len(errors)} 個，"
                f"模型列的人工覆核 {sum(1 for p in review.review_points if p.source == 'llm')} 項"
                + ("" if review.llm_ok else "（模型沒有給出可用的結果）"),
                flush=True,
            )
    finally:
        await llm.close()

    # 每個錯誤在幾次審查裡都出現
    print("\n各次審查的錯誤，以及在其他幾次也出現：")
    appear: list[int] = []
    for n, errors in enumerate(results, start=1):
        for issue in errors:
            hits = 1 + sum(
                1 for m, other in enumerate(results, start=1) if m != n and any(same_issue(issue, o) for o in other)
            )
            appear.append(hits)
            print(f"  第 {n} 次 [{issue.section_id}] {issue.kind or '-'}，{hits}/{len(results)} 次：{issue.problem[:70]}")
    if appear:
        every = sum(1 for h in appear if h == len(results))
        once = sum(1 for h in appear if h == 1)
        print(
            f"\n共 {len(appear)} 個錯誤：每次都出現 {every} 個（{every / len(appear):.0%}），"
            f"只出現一次 {once} 個（{once / len(appear):.0%}）"
        )
    args.out.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"每次的審查結果：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
