"""分析資料庫裡的 AI 工作紀錄：時間花在哪、JSON 和截斷出錯的比例、修訂前後的錯誤數。

不用跑模型，只讀 agent_runs 和 agent_events（資料庫只讀，不會修改）。改提示詞或流程前後各跑一次，就能比較。

用法（在專案根目錄）：
    uv run --project backend python scripts/analyze_runs.py --db data-demo/sagent.db
    uv run --project backend python scripts/analyze_runs.py --db data/sagent.db --model qwen3.6:35b-a3b --from-run 20
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 這些字串是 agents/ 裡記錄步驟時用的訊息
LOG_PATTERNS = {
    "JSON 格式錯誤後重試": "JSON 格式不正確",
    "輸出太長，精簡後重試": "精簡後重試",
    "JSON 兩次都失敗，改用備案": "改用備案",
    "輸出達到長度上限": "達到長度上限",
    "達到修訂次數上限": "已達修訂次數上限",
    "修改後內容變少，保留原內容": "明顯變少",
}


def _errors(review: dict) -> list[dict]:
    return [i for i in review.get("issues", []) if i.get("severity") in ("high", "medium")]


def _pct(n: float, total: float) -> str:
    return f"{n / total:.0%}" if total else "-"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--model", help="只看這個模型")
    parser.add_argument("--doc-type", help="只看這種文件（範本 key）")
    parser.add_argument("--from-run", type=int, default=0, help="只看編號大於等於這個的 AI 工作")
    args = parser.parse_args()

    db = sqlite3.connect(f"file:{args.db.as_posix()}?mode=ro", uri=True)
    where, params = ["id >= ?"], [args.from_run]
    if args.model:
        where.append("model = ?")
        params.append(args.model)
    if args.doc_type:
        where.append("doc_type = ?")
        params.append(args.doc_type)
    runs = db.execute(
        f"select id, status, model, doc_type, mode, stats from agent_runs where {' and '.join(where)} order by id",
        params,
    ).fetchall()
    if not runs:
        print("沒有符合條件的 AI 工作")
        return 1

    status = Counter(r[1] for r in runs)
    print(f"AI 工作 {len(runs)} 個：" + "、".join(f"{k} {v}" for k, v in status.most_common()))
    print("模型：" + "、".join(f"{k} {v}" for k, v in Counter(r[2] for r in runs).most_common()))

    # --- 時間花在哪 ---
    minutes: Counter[str] = Counter()
    calls: Counter[str] = Counter()
    out_tokens: Counter[str] = Counter()
    logs: Counter[str] = Counter()
    rounds: dict[int, list[dict]] = defaultdict(list)
    plan_fallback = 0
    ids = [r[0] for r in runs if r[1] == "succeeded"]
    for run_id, typ, message, data in db.execute(
        f"select run_id, type, message, data from agent_events where run_id in ({','.join('?' * len(ids))}) "
        "order by run_id, seq",
        ids,
    ):
        d = json.loads(data or "{}")
        if typ == "step_end":
            step = d.get("step", "")
            kind = step.split(":")[0]
            if kind == "review":
                kind = "review:1" if step == "review:1" else "review:2+"
            minutes[kind] += d.get("duration_ms", 0) / 60000
            calls[kind] += 1
            out_tokens[kind] += d.get("completion_tokens", 0) or 0
            if step.startswith("review:") and d.get("output"):
                rounds[run_id].append(d["output"])
            if step == "plan" and (d.get("output") or {}).get("fallback"):
                plan_fallback += 1
        elif typ == "log":
            for label, pattern in LOG_PATTERNS.items():
                if pattern in message:
                    logs[label] += 1

    names = {
        "plan": "規劃",
        "section": "撰寫章節",
        "diagram": "畫圖",
        "review:1": "第 1 輪審查",
        "review:2+": "之後的審查",
        "revise": "依審查意見修改",
        "fixdiagram": "修圖",
    }
    total = sum(minutes.values())
    print(f"\n成功的工作 {len(ids)} 個，共 {total / 60:.1f} 小時，平均每份 {total / max(len(ids), 1):.0f} 分鐘")
    print("步驟            呼叫   分鐘   佔比  平均輸出 tokens")
    for kind, m in minutes.most_common():
        print(f"{names.get(kind, kind):12} {calls[kind]:5} {m:6.0f} {_pct(m, total):>6} {out_tokens[kind] / calls[kind]:10.0f}")
    loop = minutes["revise"] + minutes["fixdiagram"] + minutes["review:2+"]
    print(f"修訂迴圈（修改、修圖、之後的審查）佔 {_pct(loop, total)}")

    # --- 出錯的比例 ---
    print("\n出錯紀錄：")
    print(f"  大綱改用範本章節：{plan_fallback} 次")
    for label in LOG_PATTERNS:
        print(f"  {label}：{logs[label]} 次")
    llm_failed = sum(1 for rs in rounds.values() for r in rs if r.get("llm_ok") is False)
    print(f"  審查模型沒有結果（只有程式檢查）：{llm_failed} / {sum(len(rs) for rs in rounds.values())} 次審查")

    # --- 修訂前後的錯誤 ---
    first = [rs[0] for rs in rounds.values() if rs]
    last = [rs[-1] for rs in rounds.values() if rs]
    revised = [rs for rs in rounds.values() if len(rs) > 1]
    e1 = sum(len(_errors(r)) for r in first)
    print(f"\n第 1 輪審查的錯誤：共 {e1} 個，平均每份 {e1 / max(len(first), 1):.1f} 個")
    src = Counter(i.get("source") for r in first for i in _errors(r))
    print("  來源：" + "、".join(f"{'程式' if k == 'check' else '模型'} {v}" for k, v in src.most_common()))
    kinds = Counter(i.get("kind") or "（舊資料沒有類型）" for r in first for i in _errors(r) if i.get("source") == "llm")
    print("  模型錯誤的類型：" + "、".join(f"{k} {v}" for k, v in kinds.most_common()))
    unstated = sum(1 for r in first for p in r.get("review_points", []) if "需求沒提到" in p.get("reason", ""))
    print(f"  需求沒提到、轉成人工覆核：{unstated} 個")
    if revised:
        before = sum(len(_errors(rs[0])) for rs in revised)
        after = sum(len(_errors(rs[-1])) for rs in revised)
        clean = sum(1 for rs in revised if not _errors(rs[-1]) and rs[-1].get("llm_ok", True))
        print(
            f"有修訂的 {len(revised)} 份：錯誤 {before} → {after} 個；修到零錯誤 {clean} 份"
            "（審查模型沒有結果的不算）"
        )
    points = sum(len(r.get("review_points", [])) for r in last)
    print(f"最後的人工覆核：平均每份 {points / max(len(last), 1):.1f} 項")
    return 0


if __name__ == "__main__":
    sys.exit(main())
