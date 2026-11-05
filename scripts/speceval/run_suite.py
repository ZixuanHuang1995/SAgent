"""SpecEval 執行器（第 13 堂）：照任務卡跑 SAgent 的 AI 流程 k 次，評 L0、L1，算 pass@1、pass^k 和信賴區間。

- 每次執行用全新的流程物件，不寫 SAgent 的資料庫（不會動到 data/）。
- 每次執行記下環境清單（manifest）：Ollama 版本、模型 digest 和量化、溫度、提示詞版本、電腦、開始時間。
- 停止條件：任務卡的 walltime_min 到了就中止，算失敗（stop_reason = walltime）。
- 時間可不可信：整個工作的時間比各步驟加總多 20% 以上（例如電腦睡眠），標 time_suspect，不進時間統計。
- 可交付（暫定，還沒有 L2）：L0 沒有 high、medium 的程式檢查錯誤，L1 通過，任務卡的 expect 都滿足。
  L2 評審跑完後用 judge.py 補上，summary 會重算。

用法（在專案根目錄；真模型很慢，先問使用者，晚上或週末跑）：
    uv run --project backend python scripts/speceval/run_suite.py --tasks speceval/tasks --times 3 --out-dir speceval-out/run-A
    uv run --project backend python scripts/speceval/run_suite.py --tasks speceval/tasks --suite tiny --out-dir speceval-out/tiny-A
    uv run --project backend python scripts/speceval/run_suite.py --tasks speceval/tasks --only FIX-idea-normal --times 1 --fake
    # 比較兩次（可交付率的配對差和信賴區間）
    uv run --project backend python scripts/analyze_runs.py --speceval speceval-out/run-A --speceval speceval-out/run-B
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import random
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from l1 import _MARK_RE, score  # noqa: E402

from sagent.agents.base import ListEmitter  # noqa: E402
from sagent.agents.doc_templates import load_templates  # noqa: E402
from sagent.agents.fake_llm import FakeLLM  # noqa: E402
from sagent.agents.llm import OllamaClient, ollama_environment  # noqa: E402
from sagent.agents.pipeline import SpecPipeline  # noqa: E402
from sagent.agents.types import PipelineOptions, RunInput  # noqa: E402
from sagent.agents.versioning import prompt_version  # noqa: E402

STOP_MESSAGES = {"已達修訂次數上限": "revision_limit", "修訂後錯誤沒有變少": "no_improvement"}


async def manifest(args) -> dict:
    info: dict = {
        "model": "fake" if args.fake else args.model,
        "temperature": args.temperature,
        "seed": args.seed,
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "started_at": datetime.now(UTC).isoformat(),
    }
    if args.fake:
        return info
    # 跟 SAgent 的 AI 工作記在 options.environment 的是同一份（C-05）
    async with httpx.AsyncClient(base_url=args.ollama.rstrip("/"), timeout=10) as c:
        try:
            info |= await ollama_environment(c, args.model)
        except (httpx.HTTPError, ValueError) as exc:
            info["ollama_error"] = str(exc)
    return info


def english_ratio(md: str) -> float:
    text = re.sub(r"```.*?```", "", md, flags=re.S)
    letters = len(re.findall(r"[A-Za-z]", text))
    cjk = len(re.findall(r"[一-鿿]", text))
    return letters / (letters + cjk) if letters + cjk else 0.0


def check_expect(card: dict, md: str) -> dict:
    s = card["scorer"]
    flat = re.sub(r"\s+", "", md)
    out: dict = {}
    hits = [x for x in s.get("must_not_include", []) if re.sub(r"\s+", "", x) in flat]
    out["must_not_include_hits"] = hits
    exp = s.get("expect", {})
    if "marks_at_least" in exp:
        out["marks"] = len(_MARK_RE.findall(md)) + md.count("- [ ]")
        out["marks_ok"] = out["marks"] >= exp["marks_at_least"]
    if exp.get("conflict"):
        a = re.sub(r"\s+", "", exp["conflict"]["original"])
        b = re.sub(r"\s+", "", exp["conflict"]["conflicting"])
        # 兩個數字都出現，而且至少有一行同時提到其中一個數字和「待確認」或「矛盾」「不一致」
        lines = [re.sub(r"\s+", "", ln) for ln in md.splitlines()]
        flagged = any((a in ln or b in ln) and re.search(r"待確認|矛盾|不一致|衝突", ln) for ln in lines)
        out["conflict_flagged"] = a in flat and b in flat and flagged
    if exp.get("language") == "zh-Hant":
        out["english_ratio"] = round(english_ratio(md), 3)
        out["language_ok"] = out["english_ratio"] < 0.3
    out["expect_ok"] = not hits and all(v for k, v in out.items() if k.endswith("_ok") or k == "conflict_flagged")
    return out


async def run_trial(card: dict, req: str, tpl, llm, args) -> dict:
    emitter = ListEmitter()
    inp = RunInput(
        mode="draft",
        project_name=card["request"].get("project_name", "SpecEval"),
        project_description="",
        phase_name="SpecEval",
        spec_title=card["request"].get("spec_title", card["id"]),
        template=tpl,
        requirement=req,
        options=PipelineOptions(
            max_revisions=card["stop"].get("max_revisions", 2),
            diagrams=tpl.allow_diagrams and not args.no_diagrams,
            temperature=args.temperature,
        ),
    )
    started = time.monotonic()
    stop_reason, result, error = "completed", None, ""
    try:
        result = await asyncio.wait_for(
            SpecPipeline(llm, emitter).run(inp), timeout=card["stop"].get("walltime_min", 120) * 60
        )
    except TimeoutError:
        stop_reason = "walltime"
    except Exception as exc:  # noqa: BLE001
        stop_reason, error = "error", str(exc)[:300]
    wall = time.monotonic() - started
    steps = sum(e["data"].get("duration_ms", 0) for e in emitter.events if e["type"] == "step_end") / 1000
    for e in emitter.events:
        for text, reason in STOP_MESSAGES.items():
            if text in e.get("message", "") and stop_reason == "completed":
                stop_reason = reason
    rec: dict = {
        "stop_reason": stop_reason,
        "error": error,
        "wall_s": round(wall, 1),
        "steps_s": round(steps, 1),
        "time_suspect": steps > 0 and wall > steps * 1.2 + 60,
    }
    if result is None:
        rec["deliverable"] = False
        return rec
    review = result.review
    checks = [i for i in (review.issues if review else []) if i.source == "check"]
    rec["l0_errors"] = sum(1 for i in checks if i.severity in ("high", "medium"))
    rec["llm_errors"] = sum(1 for i in (review.issues if review else []) if i.source == "llm" and i.severity in ("high", "medium"))
    rec["review_points"] = len(review.review_points) if review else 0
    rec["l1"] = score(result.markdown, req).to_dict()
    rec["expect"] = check_expect(card, result.markdown)
    rec["deliverable"] = rec["l0_errors"] == 0 and rec["l1"]["passed"] and rec["expect"]["expect_ok"]
    rec["markdown"] = result.markdown
    return rec


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k 的無偏估計：n 次裡成功 c 次，任取 k 次全部成功的機率。"""
    return math.comb(c, k) / math.comb(n, k) if n >= k else float("nan")


def bootstrap_ci(values: list[float], iters: int = 5000, seed: int = 0) -> tuple[float, float]:
    if not values:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(iters))
    return (round(means[int(0.025 * iters)], 3), round(means[int(0.975 * iters)], 3))


def summarize(out_dir: Path, k: int) -> dict:
    per_task = {}
    for f in sorted(out_dir.glob("*/trial-*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        ok = rec.get("deliverable_final", rec.get("deliverable", False))
        per_task.setdefault(f.parent.name, []).append(bool(ok))
    p1 = {t: sum(v) / len(v) for t, v in per_task.items()}
    pk = {t: pass_hat_k(len(v), sum(v), k) for t, v in per_task.items() if len(v) >= k}
    summary = {
        "tasks": len(per_task),
        "pass@1": round(sum(p1.values()) / len(p1), 3) if p1 else None,
        "pass@1_ci95": bootstrap_ci(list(p1.values())),
        f"pass^{k}": round(sum(pk.values()) / len(pk), 3) if pk else None,
        "per_task": {t: {"trials": len(v), "passed": sum(v)} for t, v in per_task.items()},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=Path, default=Path("speceval/tasks"))
    ap.add_argument("--only", help="只跑這些任務，逗號分隔")
    ap.add_argument("--times", type=int, help="每題跑幾次（預設 3；--suite tiny 時 1）")
    ap.add_argument(
        "--suite",
        choices=["tiny", "full"],
        help="tiny：4 張構想提案任務（4 種難度各一張）、不畫圖，每晚跑；full：全部任務卡，週末和合併前跑",
    )
    ap.add_argument("--model", default="qwen3.6:35b-a3b")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--temperature", type=float, default=0.4)
    ap.add_argument("--seed", type=int, help="固定亂數種子（C-08），回歸測試用；同一個 seed 的結果應該一樣")
    ap.add_argument("--no-diagrams", action="store_true", help="不畫圖，省時間（tiny 集合用）")
    ap.add_argument("--out-dir", type=Path, default=Path("speceval-out/run"))
    ap.add_argument("--fake", action="store_true", help="用假模型，只檢查流程")
    ap.add_argument("--summary-only", action="store_true", help="不跑，只重算 summary（judge.py 補完 L2 之後）")
    args = ap.parse_args()
    if args.times is None:
        args.times = 1 if args.suite == "tiny" else 3
    if args.summary_only:
        print(json.dumps(summarize(args.out_dir, args.times), ensure_ascii=False, indent=1))
        return 0
    cards = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(args.tasks.glob("*.yaml"))]
    if args.suite == "tiny":
        cards = [c for c in cards if c["doc_type"] == "idea_proposal"]
        args.no_diagrams = True
    if args.only:
        wanted = set(args.only.split(","))
        cards = [c for c in cards if c["id"] in wanted]
    templates = load_templates()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    info = await manifest(args)
    for card in cards:
        tpl = templates[card["doc_type"]]
        info_task = {**info, "prompt_version": prompt_version(tpl)}
        req = (args.tasks / card["request"]["requirement_file"]).read_text(encoding="utf-8")
        tdir = args.out_dir / card["id"]
        tdir.mkdir(exist_ok=True)
        for n in range(1, args.times + 1):
            path = tdir / f"trial-{n}.json"
            if path.exists():
                continue  # 中斷後重跑：跑過的不重跑
            llm = FakeLLM() if args.fake else OllamaClient(args.ollama, args.model, seed=args.seed)
            try:
                rec = await run_trial(card, req, tpl, llm, args)
            finally:
                await llm.close()
            md = rec.pop("markdown", "")
            (tdir / f"trial-{n}.md").write_text(md, encoding="utf-8")
            path.write_text(json.dumps({"task": card["id"], "trial": n, "manifest": info_task, **rec},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{card['id']:32} 第 {n} 次：{rec['stop_reason']:14} 可交付={rec.get('deliverable')} "
                  f"L0={rec.get('l0_errors')} L1={rec.get('l1', {}).get('passed')} "
                  f"expect={rec.get('expect', {}).get('expect_ok')} {rec['wall_s']}s", flush=True)
            summarize(args.out_dir, args.times)
    print(json.dumps(summarize(args.out_dir, args.times), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
