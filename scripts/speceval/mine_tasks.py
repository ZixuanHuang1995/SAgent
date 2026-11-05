"""研究計劃 C-17：從使用紀錄挑新的 SpecEval 任務（第 12 堂：資料要從真的使用情況來）。

讀 export_traces.py 匯出的 JSONL，挑出 AI 版被人大改過的工作（有章節的修改幅度 ≥ --min-edit，預設 0.3），
產生任務卡草稿：需求（接上確認大綱時的回答）、文件類型、被大改的章節、must_include 先用程式從需求抽
（帶單位的數字、「」裡的名詞），reviewed_by_human: false。人改過的版本另外存成 <id>.human.md，給審卡的人參考。

輸出放在 speceval/tasks-candidates/（不進 git：需求和人改過的文件可能有客戶資料）。
人挑過、去識別化（客戶名稱、人名、內部系統名稱換掉）、補好 must_include 之後，再搬進 speceval/tasks/。
任務卡到 20 張以上時，挑 4 張寫 holdout: true 當保留集：run_suite.py 平常不跑，只在大版本前加 --holdout 跑，
免得提示詞對常跑的任務過度最佳化。

用法（在專案根目錄；不跑模型，只讀檔案）：
    uv run --project backend python scripts/speceval/export_traces.py --db data/sagent.db --out speceval-out/traces.jsonl
    uv run --project backend python scripts/speceval/mine_tasks.py --traces speceval-out/traces.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from l1 import requirement_facts  # noqa: E402

from sagent.agents.doc_templates import load_templates  # noqa: E402
from sagent.agents.runner import requirement_with_answers  # noqa: E402

WALLTIME_MIN = {"idea_proposal": 60, "functional_spec": 150, "architecture": 120}
HOLDOUT_FROM = 20  # 任務卡到這個數量以上，要切保留集


def candidates(traces: list[dict], min_edit: float, templates: set[str]) -> list[dict]:
    """被人大改過的工作，改最多的排前面。內建範本才收（run_suite.py 只認得內建範本）。"""
    out = []
    for t in traces:
        edits = {k: v for k, v in (t.get("section_edit_ratio") or {}).items() if v >= min_edit}
        if not edits or not t.get("requirement") or t.get("doc_type") not in templates:
            continue
        out.append({**t, "_edits": dict(sorted(edits.items(), key=lambda kv: -kv[1]))})
    return sorted(out, key=lambda t: -max(t["_edits"].values()))


def card_for(trace: dict) -> tuple[dict, str]:
    """任務卡草稿和需求文字。"""
    task_id = f"RUN{trace['run_id']}-{trace['doc_type']}"
    requirement = requirement_with_answers(trace["requirement"], {"answers": trace.get("answers") or []})
    card = {
        "id": task_id,
        "doc_type": trace["doc_type"],
        "difficulty": "mined",
        "source": f"agent_runs.id={trace['run_id']}（spec {trace.get('spec_id')}，AI 版 v{trace.get('ai_version')}，"
        f"人改後 v{trace.get('human_version')}）",
        "oracle": f"{task_id}.human.md" if trace.get("human_content") else None,
        "request": {"project_name": "SpecEval", "spec_title": task_id, "requirement_file": f"{task_id}.txt"},
        "environment": {"fresh_db": True, "manifest": "auto"},
        "stop": {"max_revisions": 2, "walltime_min": WALLTIME_MIN.get(trace["doc_type"], 120)},
        "scorer": {
            "must_include": [label for label, *_ in requirement_facts(requirement)],
            "must_not_include": [],
            "expect": {},
            "reviewed_by_human": False,
        },
        # 給審卡的人看：哪幾節被大改、使用者當時的判斷（C-10）
        "mined": {
            "edited_sections": {k: round(v, 2) for k, v in trace["_edits"].items()},
            "sections_added_by_human": trace.get("sections_added_by_human") or [],
            "human_verdict": trace.get("human_verdict"),
            "human_verdict_note": trace.get("human_verdict_note") or "",
            "model": trace.get("model"),
            "prompt_version": trace.get("prompt_version"),
        },
    }
    return card, requirement


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", type=Path, default=Path("speceval-out/traces.jsonl"))
    ap.add_argument("--out-dir", type=Path, default=Path("speceval/tasks-candidates"))
    ap.add_argument("--min-edit", type=float, default=0.3, help="章節修改幅度至少多少才算被大改（0～1）")
    ap.add_argument("--limit", type=int, default=20, help="最多產生幾張")
    ap.add_argument("--tasks", type=Path, default=Path("speceval/tasks"), help="現有的任務卡，用來提醒切保留集")
    args = ap.parse_args()

    traces = [json.loads(line) for line in args.traces.read_text(encoding="utf-8").splitlines() if line.strip()]
    picked = candidates(traces, args.min_edit, set(load_templates()))[: args.limit]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for trace in picked:
        card, requirement = card_for(trace)
        (args.out_dir / f"{card['id']}.txt").write_text(requirement.strip() + "\n", encoding="utf-8")
        (args.out_dir / f"{card['id']}.yaml").write_text(
            yaml.safe_dump(card, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        if trace.get("human_content"):
            (args.out_dir / f"{card['id']}.human.md").write_text(trace["human_content"], encoding="utf-8")
        top = "、".join(f"{k} {v:.0%}" for k, v in list(card["mined"]["edited_sections"].items())[:3])
        print(f"{card['id']:32} 必要事實 {len(card['scorer']['must_include']):2}  被大改：{top}")
    print(
        f"{len(picked)} 張任務卡草稿（{len(traces)} 個工作裡挑的）：{args.out_dir}。"
        "人挑過、去識別化、補好 must_include 後再搬進 speceval/tasks/，把 reviewed_by_human 改成 true"
    )
    existing = list(args.tasks.glob("*.yaml")) if args.tasks.exists() else []
    if len(existing) >= HOLDOUT_FROM:
        holdouts = sum(1 for p in existing if (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("holdout"))
        if holdouts == 0:
            print(f"{args.tasks} 已經有 {len(existing)} 張任務卡：挑 4 張寫 holdout: true 當保留集")
    return 0


if __name__ == "__main__":
    sys.exit(main())
