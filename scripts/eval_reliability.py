"""用真模型量可靠度：同一份需求跑好幾次結果一不一致、換個說法結果會不會大變、資訊不夠時會不會編造。

參考 HAL 可靠度的三個面向：跨次一致、對擾動穩健、失敗可預期。每次跑完整的 AI 流程（Planner → Writer → Reviewer），
不寫資料庫，輸出存在 --out-dir。CPU 上一份構想提案（不畫圖、不修訂）約 20 分鐘，建議排在晚上跑。

用法（在專案根目錄）：
    uv run --project backend python scripts/eval_reliability.py --requirement-file 需求.txt --times 3 \\
        --rephrased-file 換句話說的需求.txt --sparse-file 只有兩三句的需求.txt --out-dir eval-out
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from itertools import combinations
from pathlib import Path

from sagent.agents.base import NullEmitter
from sagent.agents.doc_templates import load_templates
from sagent.agents.fake_llm import FakeLLM
from sagent.agents.llm import LLMClient, OllamaClient
from sagent.agents.pipeline import SpecPipeline
from sagent.agents.textutil import extract_item_ids
from sagent.agents.types import PipelineOptions, PipelineResult, RunInput
from sagent.services.markdown_doc import normalize_heading

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_MARK_RE = re.compile(r"[（(](假設|待確認)[）)]")
_FENCE_RE = re.compile(r"```.*?```", re.S)


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def measure(requirement: str, result: PipelineResult) -> dict:
    """一次結果的指標。編號、章節編號裡的數字不算；需求沒出現過的數字算「可能是編的」。"""
    text = _FENCE_RE.sub("", result.markdown)
    plain = re.sub(r"\b[A-Z]{1,4}-\d{2,3}\b|^#+ \d+\.|^\s*\d+[.)] ", "", text, flags=re.M)
    known = set(_NUMBER_RE.findall(requirement))
    invented = sorted({n for n in _NUMBER_RE.findall(plain) if n not in known and n not in ("0", "1")})
    review = result.review
    errors = [i for i in review.issues if i.severity in ("high", "medium")] if review else []
    return {
        "headings": [normalize_heading(s.heading) for s in result.plan.sections],
        "terms": [t.name for t in result.plan.terms],
        "items": [i.split(" ")[0] for i in extract_item_ids(text)],
        "open_questions": len(result.plan.open_questions),
        "marks": len(_MARK_RE.findall(text)),
        "invented_numbers": invented,
        "review_errors": len(errors),
        "review_points": len(review.review_points) if review else 0,
        "chars": len(text),
    }


async def run_once(llm: LLMClient, args: argparse.Namespace, requirement: str) -> PipelineResult:
    inp = RunInput(
        mode="draft",
        project_name="可靠度評估",
        project_description="",
        phase_name="需求萃取",
        spec_title=args.title,
        template=load_templates()[args.template],
        requirement=requirement,
        options=PipelineOptions(
            max_revisions=args.max_revisions, diagrams=args.max_diagrams > 0, max_diagrams=args.max_diagrams
        ),
    )
    return await SpecPipeline(llm, NullEmitter()).run(inp)


def consistency(results: list[dict]) -> dict:
    """兩兩比較章節名稱、名詞表、編號項目的重疊程度（1 是完全一樣）。"""
    pairs = list(combinations(results, 2))
    if not pairs:
        return {}
    return {
        key: round(sum(_jaccard(set(a[key]), set(b[key])) for a, b in pairs) / len(pairs), 2)
        for key in ("headings", "terms", "items")
    }


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirement-file", type=Path, required=True)
    parser.add_argument("--rephrased-file", type=Path, help="同樣的需求換一種說法或格式，量穩健性")
    parser.add_argument("--sparse-file", type=Path, help="資訊明顯不夠的需求，看會回報缺漏還是編造")
    parser.add_argument("--times", type=int, default=3)
    parser.add_argument("--template", default="idea_proposal")
    parser.add_argument("--title", default="可靠度評估")
    parser.add_argument("--max-diagrams", type=int, default=0)
    parser.add_argument("--max-revisions", type=int, default=0)
    parser.add_argument("--model", default="qwen3.6:35b-a3b")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--out-dir", type=Path, default=Path("eval-reliability"))
    parser.add_argument("--fake", action="store_true", help="用假模型，只檢查流程")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    cases = [(f"same-{n}", args.requirement_file) for n in range(1, args.times + 1)]
    if args.rephrased_file:
        cases.append(("rephrased", args.rephrased_file))
    if args.sparse_file:
        cases.append(("sparse", args.sparse_file))

    llm: LLMClient = FakeLLM() if args.fake else OllamaClient(args.ollama, args.model)
    metrics: dict[str, dict] = {}
    try:
        for name, path in cases:
            requirement = path.read_text(encoding="utf-8")
            started = time.monotonic()
            result = await run_once(llm, args, requirement)
            (args.out_dir / f"{name}.md").write_text(result.markdown, encoding="utf-8")
            m = measure(requirement, result)
            m["minutes"] = round((time.monotonic() - started) / 60, 1)
            metrics[name] = m
            print(
                f"{name}：{m['minutes']} 分鐘，{len(m['headings'])} 節，標註假設或待確認 {m['marks']} 處，"
                f"需求沒有的數字 {len(m['invented_numbers'])} 個，審查錯誤 {m['review_errors']} 個",
                flush=True,
            )
    finally:
        await llm.close()

    same = [metrics[n] for n, _ in cases if n.startswith("same-")]
    summary = {"consistency": consistency(same), "metrics": metrics}
    if "rephrased" in metrics and same:
        summary["robustness"] = consistency([same[0], metrics["rephrased"]])
    print("\n一致性（同一份需求，兩兩比較，1 是完全一樣）：", summary["consistency"])
    if "robustness" in summary:
        print("穩健性（原需求對換句話說）：", summary["robustness"])
    if "sparse" in metrics:
        s = metrics["sparse"]
        print(
            f"資訊不夠時：待確認問題 {s['open_questions']} 個、標註 {s['marks']} 處、"
            f"需求沒有的數字 {s['invented_numbers'][:10]}"
        )
    (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"結果：{args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
