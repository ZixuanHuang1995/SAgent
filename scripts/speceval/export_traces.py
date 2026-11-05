"""第 11 堂（資料）：把 SAgent 已經存的 AI 工作紀錄整理成一份資料集（JSONL），給評估、提示詞優化、few-shot 用。

每一列是一個成功的 AI 工作：
- 輸入：需求、文件類型、範本、模型、提示詞版本、先確認大綱的回答
- 過程：大綱、每一輪審查（錯誤、人工覆核）、各步驟時間
- 回饋：使用者在「審查」分頁的標記（fixed 已處理／false 誤報／skip 略過）
- 結果：AI 版本，以及人改過的最後版本（下一個 AI 版本之前的最後一個人工版本），逐節修改幅度

SAgent 只存輸出、不存提示詞，所以這份資料集不能直接重放；要重放得用同一個 prompt_version 的程式重跑。
資料庫只讀。人改過的文件可能有客戶資料，輸出檔不要放進 git、不要傳到外部服務。

用法（在專案根目錄）：
    uv run --project backend python scripts/speceval/export_traces.py --db data/sagent.db --out speceval-out/traces.jsonl
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from analyze_edits import compare  # noqa: E402


def _json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def human_version(db: sqlite3.Connection, spec_id: int, ai_no: int) -> tuple[int, str] | None:
    """AI 版本之後、下一個 AI 版本之前的最後一個人工版本。"""
    rows = db.execute(
        "select version_no, source, content from spec_versions where spec_id = ? and version_no > ? "
        "order by version_no",
        (spec_id, ai_no),
    ).fetchall()
    last = None
    for no, source, content in rows:
        if source == "agent":
            break
        if source == "manual":
            last = (no, content)
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("speceval-out/traces.jsonl"))
    args = ap.parse_args()
    db = sqlite3.connect(f"file:{args.db.as_posix()}?mode=ro", uri=True)
    cols = {r[1] for r in db.execute("pragma table_info(agent_runs)")}
    want = [
        c
        for c in (
            "id", "spec_id", "mode", "status", "model", "doc_type", "requirement", "options", "plan", "review",
            "review_checked", "review_labels", "stats", "result_version_no", "base_version_no",
        )
        if c in cols
    ]
    runs = db.execute(f"select {', '.join(want)} from agent_runs where status = 'succeeded' order by id").fetchall()
    counts: Counter[str] = Counter()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for row in runs:
            r = dict(zip(want, row, strict=True))
            for k in ("options", "plan", "review", "review_checked", "review_labels", "stats"):
                r[k] = _json(r.get(k))
            ai = None
            if r.get("result_version_no"):
                got = db.execute(
                    "select content from spec_versions where spec_id = ? and version_no = ?",
                    (r["spec_id"], r["result_version_no"]),
                ).fetchone()
                ai = got[0] if got else None
            human = human_version(db, r["spec_id"], r["result_version_no"]) if ai else None
            sections, added = compare(ai, human[1]) if ai and human else ({}, [])
            stored = r.get("review") or {}
            # agent_runs.review = {"final": 最後一次審查, "rounds": [每一輪]}；舊資料直接是審查結果
            review = stored.get("final") or stored if isinstance(stored, dict) else {}
            rounds = stored.get("rounds", []) if isinstance(stored, dict) else []
            labels = r.get("review_labels") or {}
            checked = r.get("review_checked") or []
            items = []
            for kind, key in (("issue", "issues"), ("point", "review_points")):
                for i, it in enumerate(review.get(key, []) or []):
                    tag = f"{kind}:{i}"
                    label = labels.get(tag) or ("fixed" if tag in checked else None)
                    items.append({**it, "item_key": tag, "label": label})
            opts = r.get("options") or {}
            rec = {
                "run_id": r["id"],
                "spec_id": r["spec_id"],
                "mode": r.get("mode"),
                "doc_type": r.get("doc_type"),
                "model": r.get("model"),
                "prompt_version": opts.get("prompt_version"),
                "temperature": opts.get("temperature"),
                "requirement": r.get("requirement"),
                "answers": opts.get("answers"),
                "plan": r.get("plan"),
                "review_items": items,
                "review_rounds": [
                    {"round": rd.get("round"), "errors": sum(1 for i in rd.get("issues", []) if i.get("severity") in ("high", "medium"))}
                    for rd in rounds
                ],
                "stats": r.get("stats"),
                "ai_version": r.get("result_version_no"),
                "ai_content": ai,
                "human_version": human[0] if human else None,
                "human_content": human[1] if human else None,
                "section_edit_ratio": {k: round(v, 3) for k, v in sections.items()},
                "sections_added_by_human": added,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            counts["runs"] += 1
            counts["with_human_edit"] += bool(human)
            counts["review_items"] += len(items)
            counts["labeled_items"] += sum(1 for it in items if it["label"])
            counts["false_positive_labels"] += sum(1 for it in items if it["label"] == "false")
    print(json.dumps(counts, ensure_ascii=False))
    print(f"結果：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
