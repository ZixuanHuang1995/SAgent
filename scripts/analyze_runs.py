"""分析資料庫裡的 AI 工作紀錄：時間花在哪、JSON 和截斷出錯的比例、修訂前後的錯誤數。

另外兩段：
- 審查準確度：使用者在「審查」分頁把項目標成已處理、誤報或略過，算出 Reviewer 列的錯誤有幾成是真的
- 監看指標：AI 產出文件的標註密度、表格數、章節長度。改提示詞、優化時，錯誤數變少不一定是寫得更好，
  也可能是模型學會走捷徑（全部標「待確認」就不會被抓「寫了需求沒提的內容」，少畫表格就不會有欄數錯誤），
  這幾個數字明顯偏離之前的時候要打開文件看

不用跑模型，只讀 agent_runs、agent_events 和 spec_versions（資料庫只讀，不會修改）。改提示詞或流程前後各跑一次，就能比較。

這裡的錯誤數只用來診斷（哪一步出錯、修訂有沒有用），不能拿來判斷改好或改壞：SpecEval 的對照組裡，
每節只寫待確認的空洞假文件錯誤數最少。決定要不要合併時看 SpecEval 的可交付率（--speceval，研究計劃 C-02）。

用法（在專案根目錄）：
    uv run --project backend python scripts/analyze_runs.py --db data-demo/sagent.db
    uv run --project backend python scripts/analyze_runs.py --db data/sagent.db --model qwen3.6:35b-a3b --from-run 20
    # SpecEval 的可交付率；給兩個目錄時印配對差（B − A）和 95% 信賴區間
    uv run --project backend python scripts/analyze_runs.py --speceval speceval-out/run-A --speceval speceval-out/run-B
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# 這些字串是 agents/ 裡記錄步驟時用的訊息
LOG_PATTERNS = {
    "JSON 格式錯誤後重試": "JSON 格式不正確",
    "輸出太長，精簡後重試": "精簡後重試",
    "JSON 兩次都失敗，改用備案": "改用備案",
    "輸出達到長度上限": "達到長度上限",
    "達到修訂次數上限": "已達修訂次數上限",
    "錯誤沒有變少，停止修訂": "修訂後錯誤沒有變少",
    "一輪最多改 4 節，有章節沒改到": "這一輪最多修改",
    "修改後內容變少，保留原內容": "明顯變少",
    "沒有可以自動修訂的章節": "沒有可以自動修訂的章節",
    "第一輪審查太長，分批審查（C-14）": "批審查",
}
# 這幾種是停止原因。新的工作記在 agent_runs.stop_reason（C-06），只有舊紀錄才從訊息推斷
STOP_LOG_LABELS = {"達到修訂次數上限", "錯誤沒有變少，停止修訂", "沒有可以自動修訂的章節"}
STOP_NAMES = {
    "passed": "審查沒有錯誤",
    "revision_limit": "達到修訂次數上限",
    "no_improvement": "修訂後錯誤沒有變少",
    "nothing_to_revise": "沒有可以自動修訂的章節",
    "failed": "失敗",
    "cancelled": "取消",
    "walltime": "超過整個工作的時間上限",
}
# 時間不可信（C-07）：整個工作的時間比各步驟加總多這麼多，多半是電腦睡眠
SUSPECT_RATIO = 1.2
SUSPECT_EXTRA_SECONDS = 60


def _errors(review: dict) -> list[dict]:
    return [i for i in review.get("issues", []) if i.get("severity") in ("high", "medium")]


def _pct(n: float, total: float) -> str:
    return f"{n / total:.0%}" if total else "-"


_MARK_RE = re.compile(r"[（(](假設|待確認)[）)]")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}", re.M)
_SECTION_RE = re.compile(r"^## ", re.M)


def doc_signals(markdown: str) -> dict[str, float]:
    """一份文件的監看指標：每千字幾個（假設）（待確認）、幾個表格、平均每節幾個字。"""
    chars = len(re.sub(r"\s", "", markdown)) or 1
    sections = len(_SECTION_RE.findall(markdown)) or 1
    return {
        "marks_per_1k": len(_MARK_RE.findall(markdown)) * 1000 / chars,
        "tables": len(_TABLE_SEP_RE.findall(markdown)),
        "chars_per_section": chars / sections,
    }


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def run_wall_seconds(row: dict) -> float | None:
    """整個工作實際經過的秒數。新的工作有 wall_seconds（C-06）；舊紀錄用 finished_at - started_at，
    但先確認大綱的舊工作會把等使用者的時間算進去，不算。"""
    if row.get("wall_seconds") is not None:
        return float(row["wall_seconds"])
    opts = json.loads(row.get("options") or "{}") or {}
    if opts.get("plan_first"):
        return None
    start, end = _parse_time(row.get("started_at")), _parse_time(row.get("finished_at"))
    return (end - start).total_seconds() if start and end else None


def time_suspect(row: dict) -> bool:
    """時間不可信（C-07）：整個工作的時間比各 agent 的時間加總多 20% 以上、而且多 60 秒以上。"""
    wall = run_wall_seconds(row)
    stats = json.loads(row.get("stats") or "{}") or {}
    steps = sum(float((v or {}).get("seconds") or 0) for v in stats.values())
    if wall is None or steps <= 0:
        return False
    return wall > steps * SUSPECT_RATIO and wall - steps > SUSPECT_EXTRA_SECONDS


def _print_signals(db: sqlite3.Connection, ids: list[int]) -> None:
    rows = db.execute(
        f"select v.content from agent_runs r join spec_versions v "
        f"on v.spec_id = r.spec_id and v.version_no = r.result_version_no where r.id in ({','.join('?' * len(ids))})",
        ids,
    ).fetchall()
    if not rows:
        return
    signals = [doc_signals(r[0]) for r in rows]
    avg = {k: sum(s[k] for s in signals) / len(signals) for k in signals[0]}
    print(f"\n監看指標（{len(signals)} 份 AI 產出的文件，平均）：")
    print(f"  （假設）（待確認）每千字 {avg['marks_per_1k']:.1f} 個、表格 {avg['tables']:.1f} 個、每節 {avg['chars_per_section']:.0f} 字")
    print("  改提示詞後這幾個數字明顯偏離，就算錯誤數變少，也要打開文件看是不是在走捷徑")


LABEL_NAMES = {"fixed": "已處理", "false": "誤報", "skip": "略過"}
# 需求追溯的缺口（reviewer.py 的 NUMBER_GAP_REASON、ITEM_GAP_REASON）
GAP_REASON_PREFIXES = ("文件裡找不到這個數字", "文件裡幾乎找不到對應的內容")


def _print_precision(db: sqlite3.Connection, ids: list[int]) -> None:
    """使用者標記的審查項目：Reviewer 列的錯誤有幾成是真的（已處理 ÷（已處理＋誤報））。"""
    columns = {r[1] for r in db.execute("pragma table_info(agent_runs)")}
    has_labels = "review_labels" in columns
    sql = f"select review, review_checked{', review_labels' if has_labels else ''} from agent_runs where id in "
    counts: Counter[tuple[str, str]] = Counter()
    for row in db.execute(sql + f"({','.join('?' * len(ids))})", ids):
        final = (json.loads(row[0] or "{}") or {}).get("final") or {}
        checked = json.loads(row[1] or "[]") or []
        labels = (json.loads(row[2] or "{}") or {}) if has_labels else {}
        for key in checked:
            kind, _, n = key.partition(":")
            items = final.get("issues" if kind == "issue" else "review_points") or []
            if not n.isdigit() or int(n) >= len(items):
                continue
            item = items[int(n)]
            if kind == "issue":
                group = f"錯誤・{'程式檢查' if item.get('source') == 'check' else '模型'}"
                if item.get("source") == "llm" and item.get("kind"):
                    group += f"・{item['kind']}"
            elif str(item.get("reason", "")).startswith(GAP_REASON_PREFIXES):
                group = "人工覆核・需求追溯"
            else:
                group = f"人工覆核・{'文件標註' if item.get('source') == 'check' else '模型'}"
            counts[(group, labels.get(key, "fixed"))] += 1
    print("\n審查準確度（使用者在「審查」分頁標的）：")
    if not counts:
        print("  還沒有標記：在 Spec 頁的「審查」分頁把項目標成已處理、誤報或略過，就會出現在這裡")
        return
    if not has_labels:
        print("  這個資料庫還沒有 review_labels 欄位（伺服器更新後啟動一次就有），處理過的都當成已處理")
    print("  已處理  誤報  略過  準確度  項目")
    for group in sorted({g for g, _ in counts}):
        fixed, false, skip = (counts[(group, k)] for k in LABEL_NAMES)
        print(f"  {fixed:6} {false:5} {skip:5} {_pct(fixed, fixed + false):>7}  {group}")


def _load_speceval(run_dir: Path) -> dict | None:
    path = run_dir / "summary.json"
    if not path.exists():
        print(f"{run_dir}：找不到 summary.json（run_suite.py 跑完會產生；只有 trial 檔時用 --summary-only 重算）")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _rates(summary: dict) -> dict[str, float]:
    return {t: v["passed"] / v["trials"] for t, v in (summary.get("per_task") or {}).items() if v.get("trials")}


def paired_diff(a: dict[str, float], b: dict[str, float], iters: int = 5000, seed: int = 0) -> dict:
    """兩次 SpecEval 都有的題目，每題通過率的差（B − A）：平均和配對 bootstrap 95% 信賴區間。"""
    common = sorted(set(a) & set(b))
    diffs = [b[t] - a[t] for t in common]
    if not diffs:
        return {"tasks": 0, "mean": None, "ci95": None}
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(iters))
    return {
        "tasks": len(diffs),
        "mean": round(sum(diffs) / len(diffs), 3),
        "ci95": (round(means[int(0.025 * iters)], 3), round(means[int(0.975 * iters)], 3)),
        "per_task": dict(zip(common, (round(d, 3) for d in diffs), strict=True)),
    }


def _print_speceval(dirs: list[Path]) -> int:
    """SpecEval 的主要指標（C-02）：可交付率 pass@1、信賴區間、pass^k、每題通過次數。"""
    summaries = []
    for d in dirs:
        s = _load_speceval(d)
        if s is None:
            return 1
        summaries.append((d, s))
        k = next((key for key in s if key.startswith("pass^")), "pass^k")
        print(f"\nSpecEval：{d}")
        print(f"  題目 {s.get('tasks')} 題，可交付率 pass@1 = {s.get('pass@1')}（95% 信賴區間 {s.get('pass@1_ci95')}），{k} = {s.get(k)}")
        for task, v in sorted((s.get("per_task") or {}).items()):
            print(f"  {v['passed']}/{v['trials']}  {task}")
    if len(summaries) >= 2:
        (da, a), (db_, b) = summaries[0], summaries[-1]
        diff = paired_diff(_rates(a), _rates(b))
        print(f"\n配對差（{db_.name} − {da.name}，兩邊都有的 {diff['tasks']} 題）：")
        if diff["mean"] is None:
            print("  沒有共同的題目")
            return 1
        lo, hi = diff["ci95"]
        verdict = "變好" if lo > 0 else "變差" if hi < 0 else "看不出差別（信賴區間包含 0）"
        print(f"  平均 {diff['mean']:+.3f}，95% 信賴區間 ({lo:+.3f}, {hi:+.3f})：{verdict}")
        for task, d in diff["per_task"].items():
            if d:
                print(f"  {d:+.2f}  {task}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path)
    parser.add_argument(
        "--speceval",
        type=Path,
        action="append",
        help="SpecEval 的結果目錄（run_suite.py 的 --out-dir）。給兩次時印配對差（第二個 − 第一個）",
    )
    parser.add_argument("--model", help="只看這個模型")
    parser.add_argument("--doc-type", help="只看這種文件（範本 key）")
    parser.add_argument("--from-run", type=int, default=0, help="只看編號大於等於這個的 AI 工作")
    parser.add_argument(
        "--group-by",
        choices=["prompt"],
        help="prompt：依提示詞、範本和程式的版本分組，比較改提示詞前後的時間和錯誤數",
    )
    args = parser.parse_args()
    if not args.db and not args.speceval:
        parser.error("要給 --db 或 --speceval")
    if args.speceval:
        code = _print_speceval(args.speceval)
        if not args.db:
            return code

    db = sqlite3.connect(f"file:{args.db.as_posix()}?mode=ro", uri=True)
    where, params = ["id >= ?"], [args.from_run]
    if args.model:
        where.append("model = ?")
        params.append(args.model)
    if args.doc_type:
        where.append("doc_type = ?")
        params.append(args.doc_type)
    runs = db.execute(
        f"select id, status, model, doc_type, mode, stats, options from agent_runs where {' and '.join(where)} "
        "order by id",
        params,
    ).fetchall()
    if not runs:
        print("沒有符合條件的 AI 工作")
        return 1

    status = Counter(r[1] for r in runs)
    print(f"AI 工作 {len(runs)} 個：" + "、".join(f"{k} {v}" for k, v in status.most_common()))
    print("模型：" + "、".join(f"{k} {v}" for k, v in Counter(r[2] for r in runs).most_common()))

    # 舊資料庫可能還沒有這些欄位（伺服器更新後啟動一次就有）
    columns = {r[1] for r in db.execute("pragma table_info(agent_runs)")}
    extra_cols = [c for c in ("stop_reason", "wall_seconds", "started_at", "finished_at") if c in columns]
    info: dict[int, dict] = {}
    for row in db.execute(
        f"select id, stats, options{''.join(', ' + c for c in extra_cols)} from agent_runs "
        f"where id in ({','.join('?' * len(runs))})",
        [r[0] for r in runs],
    ):
        info[row[0]] = dict(zip(["id", "stats", "options", *extra_cols], row, strict=True))

    # --- 停止原因（C-06） ---
    reasons = Counter(info[r[0]].get("stop_reason") for r in runs if info[r[0]].get("stop_reason"))
    if reasons:
        print("停止原因：" + "、".join(f"{STOP_NAMES.get(k, k)} {v}" for k, v in reasons.most_common()))
    legacy = {r[0] for r in runs if not info[r[0]].get("stop_reason")}

    # --- 時間花在哪 ---
    minutes: Counter[str] = Counter()
    calls: Counter[str] = Counter()
    out_tokens: Counter[str] = Counter()
    logs: Counter[str] = Counter()
    rounds: dict[int, list[dict]] = defaultdict(list)
    run_minutes: Counter[int] = Counter()
    plan_fallback = 0
    ids = [r[0] for r in runs if r[1] == "succeeded"]
    # 時間不可信的工作（C-07）不算進時間統計，另外列出
    suspect = [i for i in ids if time_suspect(info[i])]
    for run_id, typ, message, data in db.execute(
        f"select run_id, type, message, data from agent_events where run_id in ({','.join('?' * len(ids))}) "
        "order by run_id, seq",
        ids,
    ):
        d = json.loads(data or "{}")
        if typ == "step_end":
            if "duration_ms" not in d:
                continue  # 伺服器重啟時補上的「中斷」，不是真的執行了這一步
            step = d.get("step", "")
            kind = step.split(":")[0]
            if kind == "review":
                kind = "review:1" if step == "review:1" else "review:2+"
            if run_id not in suspect:
                minutes[kind] += d.get("duration_ms", 0) / 60000
                run_minutes[run_id] += d.get("duration_ms", 0) / 60000
                calls[kind] += 1
                out_tokens[kind] += d.get("completion_tokens", 0) or 0
            if step.startswith("review:") and d.get("output"):
                if step == "review:1":
                    rounds[run_id] = []  # 續跑時從第一次審查重來，只算最後那一次
                rounds[run_id].append(d["output"])
            if step == "plan" and (d.get("output") or {}).get("fallback"):
                plan_fallback += 1
        elif typ == "log":
            for label, pattern in LOG_PATTERNS.items():
                if pattern in message and (label not in STOP_LOG_LABELS or run_id in legacy):
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
    timed = len(ids) - len(suspect)
    print(f"\n成功的工作 {len(ids)} 個，共 {total / 60:.1f} 小時，平均每份 {total / max(timed, 1):.0f} 分鐘")
    if suspect:
        print(
            f"時間不可信 {len(suspect)} 份（整個工作的時間比各步驟加總多 20% 以上、而且多 60 秒以上，"
            f"多半是電腦睡眠），不算進時間統計：工作 {', '.join(str(i) for i in suspect)}"
        )
    print("步驟            呼叫   分鐘   佔比  平均輸出 tokens")
    for kind, m in minutes.most_common():
        print(f"{names.get(kind, kind):12} {calls[kind]:5} {m:6.0f} {_pct(m, total):>6} {out_tokens[kind] / calls[kind]:10.0f}")
    loop = minutes["revise"] + minutes["fixdiagram"] + minutes["review:2+"]
    print(f"修訂迴圈（修改、修圖、之後的審查）佔 {_pct(loop, total)}")

    # --- 出錯的比例 ---
    print("\n出錯紀錄：")
    print(f"  大綱改用範本章節：{plan_fallback} 次")
    for label in LOG_PATTERNS:
        note = "（只算沒有 stop_reason 的舊紀錄）" if label in STOP_LOG_LABELS else ""
        print(f"  {label}：{logs[label]} 次{note}")
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

    if ids:
        _print_precision(db, ids)
        _print_signals(db, ids)
    if args.group_by == "prompt":
        _print_by_prompt(runs, run_minutes, rounds, set(suspect))
    return 0


def _version_label(options_json: str | None, model: str = "") -> str:
    opts = json.loads(options_json or "{}") or {}
    v = opts.get("prompt_version") or {}
    label = (
        f"提示詞 {v.get('prompts', '')[:7]}、範本 {v.get('template', '')[:7]}、程式 {v.get('code') or '?'}"
        if v
        else "（沒有記錄，舊的工作）"
    )
    # 同一個模型名稱，ollama pull 之後 digest 會變，結果也可能不一樣，要分開算（C-05）
    digest = (opts.get("environment") or {}).get("digest", "")
    return f"{label}｜{model or '?'} {digest[:12] if digest else '（沒有 digest）'}"


def _print_by_prompt(
    runs: list, run_minutes: Counter, rounds: dict[int, list[dict]], suspect: set[int] | None = None
) -> None:
    """改提示詞前後的比較。同一個版本、同一個模型 digest 的工作放在一起，只算成功的。"""
    groups: dict[str, list[int]] = defaultdict(list)
    for run_id, status, model, *_rest, options in runs:
        if status == "succeeded":
            groups[_version_label(options, model)].append(run_id)
    print("\n依提示詞版本和模型（digest 不同分開算）：")
    print("  份數  平均分鐘  第 1 輪錯誤  最後錯誤  版本｜模型 digest")
    for label, ids in groups.items():
        rs = [rounds[i] for i in ids if rounds.get(i)]
        first = sum(len(_errors(r[0])) for r in rs) / max(len(rs), 1)
        last = sum(len(_errors(r[-1])) for r in rs) / max(len(rs), 1)
        timed = [i for i in ids if i not in (suspect or set())]  # 時間不可信的不算平均分鐘
        avg = sum(run_minutes[i] for i in timed) / max(len(timed), 1)
        print(f"  {len(ids):4} {avg:9.0f} {first:12.1f} {last:9.1f}  {label}")


if __name__ == "__main__":
    sys.exit(main())
