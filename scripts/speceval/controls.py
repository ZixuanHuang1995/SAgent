"""SpecEval 對照組實驗：oracle 文件和 trivial 代理，在 SAgent 自己的程式檢查（L0）和 L1 上各得幾分。

oracle = scripts/demo_content 的內容檔（人審過的示範文件）。trivial 代理把每一節換成沒有價值的內容，
用 SAgent 真正的 AI 流程（Planner → Writer → Reviewer，腳本模型）跑一次，修訂 0 輪。
審查模型的部分預設一律回答「沒有錯誤」，所以 review 裡的錯誤全部來自程式檢查（static_checks），
這樣才看得出程式檢查和需求追溯本身擋不擋得住這些代理。
加 --review-model 時，審查改由真的 Ollama 模型做（其他步驟照樣用腳本），看 SAgent 的 Reviewer 擋不擋得住
（一份約 2～10 分鐘，22 份 × 7 種約 5～20 小時，可以用 --only 先跑幾份）。

用法（在專案根目錄）：
    uv run --project backend python scripts/speceval/controls.py --out speceval-out/controls.json
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))

from check_demo_content import doc_type_of, phase_of, run_options, templates  # noqa: E402
from demo_llm import EMPTY_REVIEW, ContentBook, ScriptedLLM  # noqa: E402
from demo_scenarios import ALGO_SCENARIOS, SCENARIOS  # noqa: E402
from l1 import score  # noqa: E402

from sagent.agents.base import ListEmitter  # noqa: E402
from sagent.agents.llm import OllamaClient  # noqa: E402
from sagent.agents.pipeline import SpecPipeline  # noqa: E402
from sagent.agents.reviewer import requirement_items  # noqa: E402
from sagent.agents.types import PipelineOptions, RunInput  # noqa: E402

GAP_REASONS = ("文件裡找不到這個數字", "文件裡幾乎找不到對應的內容")
HOLLOW_TEXT = "需求沒有提供這一節需要的資訊，以下項目都待確認，請業務單位補充後再撰寫。（待確認）"


def section_ids(tpl, heading: str) -> str:
    for sec in tpl.sections:
        if sec.heading == heading:
            return sec.ids or ""
    return ""


def requirement_of(sc, key: str) -> str:
    """跟 seed_demo.py 一樣：功能規格書的需求 = 構想（這裡用構想的需求原文代替構想文件）+ 訪談補充。"""
    if key == "feature" and "feature_extra" in sc.texts:
        return sc.texts.get("idea", "") + "\n" + sc.texts["feature_extra"]
    return sc.texts.get(key) or ""


def make_variant(name: str, oracle: dict, requirement: str, tpl) -> dict:
    data = copy.deepcopy(oracle)
    data["review"] = EMPTY_REVIEW
    if name == "oracle":
        return data
    plan = data["plan"]
    plan["open_questions"] = []
    for sec in plan["sections"]:
        sec["diagrams"] = []
    data["diagrams"] = []
    sentences = requirement_items(requirement) or [requirement.strip()]
    bodies = []
    for sec in plan["sections"]:
        tsec = next((t for t in tpl.sections if t.heading == sec["heading"]), None)
        prefix = section_ids(tpl, sec["heading"])
        if name == "empty":
            body = ""
        elif name == "tbd_short":
            body = "（待確認）"
        elif name == "skeleton":
            body = tsec.goal if tsec else sec.get("goal", "")
        elif name == "paste_req":
            body = requirement.strip()
        elif name == "paste_req_ids":
            if prefix:
                body = "\n".join(f"- {prefix}-{i:02d} {s}" for i, s in enumerate(sentences, 1))
            else:
                body = requirement.strip()
        elif name == "hollow_tbd":
            body = HOLLOW_TEXT
            if prefix:
                body += "\n\n" + "\n".join(f"- {prefix}-{i:02d} 項目內容（待確認）" for i in range(1, 4))
        else:
            raise ValueError(name)
        bodies.append(body)
    data["sections"] = bodies
    return data


class HybridLLM(ScriptedLLM):
    """規劃、撰寫、畫圖用腳本；審查交給真的模型。"""

    reviewer: OllamaClient | None = None

    async def chat(self, messages, **kw):
        if self.reviewer is not None and kw.get("purpose", "").startswith("reviewer"):
            return await self.reviewer.chat(messages, **kw)
        return await super().chat(messages, **kw)


VARIANTS = ["oracle", "empty", "tbd_short", "skeleton", "paste_req", "paste_req_ids", "hollow_tbd"]


async def run_one(sc, key: str, variant: dict, tpls, root: Path, reviewer: OllamaClient | None = None) -> dict:
    doc_type = doc_type_of(sc, key)
    tpl = tpls[doc_type]
    opts = run_options(sc, doc_type)
    (root / sc.code).mkdir(parents=True, exist_ok=True)
    (root / sc.code / f"{key}.json").write_text(json.dumps(variant, ensure_ascii=False), encoding="utf-8")
    book = ContentBook(root)
    requirement = requirement_of(sc, key)
    inp = RunInput(
        mode="draft",
        project_name=sc.name,
        project_description=sc.description,
        phase_name=phase_of(sc, key),
        spec_title=sc.titles[key],
        template=tpl,
        requirement=requirement,
        existing_content="",
        options=PipelineOptions(
            uml_syntax=opts.get("uml_syntax", "auto"),
            max_revisions=0,
            diagrams=tpl.allow_diagrams,
            max_diagrams=opts.get("max_diagrams"),
        ),
    )
    emitter = ListEmitter()
    llm = HybridLLM(book, sc.code, key, model="script", delay=0)
    llm.reviewer = reviewer
    result = await SpecPipeline(llm, emitter).run(inp)
    review = result.review
    issues = review.issues if review else []
    points = review.review_points if review else []
    l1_req = score(result.markdown, requirement)
    # 專案脈絡：同一個專案所有文件的需求原文（真的系統裡還有前面寫好的文件和詞彙表）
    l1 = score(result.markdown, requirement, context="\n".join(v for v in sc.texts.values() if isinstance(v, str)))
    return {
        "doc_type": doc_type,
        "l0_high": sum(1 for i in issues if i.severity == "high"),
        "l0_medium": sum(1 for i in issues if i.severity == "medium"),
        "l0_errors": review.error_count if review else 0,
        "product_pass": bool(review) and review.error_count == 0,
        "llm_errors": sum(1 for i in issues if i.source == "llm" and i.severity in ("high", "medium")),
        "req_gaps": sum(1 for p in points if p.source == "check" and p.reason.startswith(GAP_REASONS)),
        "marked_points": sum(
            1 for p in points if p.source == "check" and not p.reason.startswith(GAP_REASONS)
        ),
        "fallback": result.plan.fallback,
        "l1": l1.to_dict(),
        "invented_req_only": l1_req.invented,
        "chars": len(re.sub(r"\s+", "", result.markdown)),
        "markdown": result.markdown,
    }


def save_for_judge(root: Path, task_id: str, sc, key: str, requirement: str, md: str, row: dict) -> None:
    tasks = root / "_tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / f"{task_id}.txt").write_text(requirement, encoding="utf-8")
    card = {"id": task_id, "request": {"requirement_file": f"{task_id}.txt", "spec_title": sc.titles[key]}}
    (tasks / f"{task_id}.yaml").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    tdir = root / task_id
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "trial-1.md").write_text(md, encoding="utf-8")
    rec = {"task": task_id, "trial": 1, "deliverable": row.get("product_pass", False) and row["l1"]["passed"]}
    (tdir / "trial-1.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", type=Path, default=SCRIPTS / "demo_content")
    ap.add_argument("--out", type=Path, default=Path("speceval-out/controls.json"))
    ap.add_argument("--review-model", help="審查改用這個 Ollama 模型，例如 qwen3.6:35b-a3b")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--only", help="只跑這些任務，例如 FIX/feature,PUR/idea")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument(
        "--save-dir",
        type=Path,
        help="把每份文件存成 run_suite.py 的格式（加上 _tasks 任務卡），之後可以用 judge.py 評審",
    )
    args = ap.parse_args()
    tpls = templates()
    book = ContentBook(args.content)
    rows = []
    jobs = [(sc, k) for sc in SCENARIOS for k in ("idea", "feature", "architecture", "database", "change")]
    jobs += [(sc, k) for sc in ALGO_SCENARIOS for k in sc.doc_types]
    if args.only:
        wanted = set(args.only.split(","))
        jobs = [(sc, k) for sc, k in jobs if f"{sc.code}/{k}" in wanted]
    reviewer = OllamaClient(args.ollama, args.review_model) if args.review_model else None
    with tempfile.TemporaryDirectory() as tmp:
        for sc, key in jobs:
            if not book.path(sc.code, key).exists():
                continue
            oracle = book.load(sc.code, key)
            tpl = tpls[doc_type_of(sc, key)]
            requirement = requirement_of(sc, key)
            for name in args.variants.split(","):
                variant = make_variant(name, oracle, requirement, tpl)
                root = Path(tmp) / name
                try:
                    row = await run_one(sc, key, variant, tpls, root, reviewer)
                except Exception as exc:  # noqa: BLE001
                    row = {"error": str(exc)[:200]}
                row.update(task=f"{sc.code}/{key}", variant=name)
                md = row.pop("markdown", "")
                if args.save_dir and md:
                    save_for_judge(args.save_dir, f"{sc.code}-{key}-{name}", sc, key, requirement, md, row)
                rows.append(row)
                print(
                    f"{sc.code}/{key:12} {name:14} L0錯誤={row.get('l0_errors')} "
                    f"產品通過={row.get('product_pass')} 需求缺口={row.get('req_gaps')} "
                    f"L1={row.get('l1', {}).get('passed')} cov={row.get('l1', {}).get('coverage')} "
                    f"copy={row.get('l1', {}).get('copy_ratio')} inv={len(row.get('l1', {}).get('invented', []))} "
                    f"hollow={len(row.get('l1', {}).get('hollow', []))}",
                    flush=True,
                )
    if reviewer is not None:
        await reviewer.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"結果：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
