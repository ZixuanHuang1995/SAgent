"""AI 寫的版本被人改了多少：把每份 Spec 的 AI 版本和接著的人工版本配成一對，逐節算修改幅度。

SAgent 每次存檔都是新版本，所以「AI 產出 → 人改過」本來就存在資料庫裡，這是最便宜、最真實的回饋：
哪種文件、哪一節最常被大改，就是提示詞、範本最該改的地方（也是 Reviewer 沒抓到的問題）。

配對規則：AI 版本（source = agent）之後、下一個 AI 版本之前的最後一個人工版本（source = manual）。
人改了好幾次，只看最後一次，代表人覺得可以了的樣子。還原（restore）、匯入（import）不算人工修改。

修改幅度 = 1 − 兩版文字的相似度（difflib），0 是沒改、1 是整節重寫；章節用去掉編號的標題比對。
不跑模型，資料庫只讀。

用法（在專案根目錄）：
    uv run --project backend python scripts/analyze_edits.py --db data/sagent.db
    uv run --project backend python scripts/analyze_edits.py --db data/sagent.db --doc-type functional_spec --top 15
"""

from __future__ import annotations

import argparse
import difflib
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sagent.services.markdown_doc import normalize_heading, parse_markdown, strip_heading_number

HEAVY = 0.3  # 改了三成以上算「大改」


@dataclass
class Pair:
    spec_id: int
    title: str
    doc_type: str
    ai_no: int
    human_no: int
    # 章節標題 → 修改幅度；AI 有、人刪掉的是 1.0；人新增的另外記
    sections: dict[str, float] = field(default_factory=dict)
    added: list[str] = field(default_factory=list)
    whole: float = 0.0


def edit_ratio(a: str, b: str, exact: bool = True) -> float:
    """exact=False 給整份文件用：上萬字時 autojunk 關掉會很慢，結果只差一點。"""
    a, b = a.strip(), b.strip()
    if a == b:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, a, b, autojunk=not exact).ratio()


def compare(ai: str, human: str) -> tuple[dict[str, float], list[str]]:
    before = {normalize_heading(s.heading): s for s in parse_markdown(ai).sections}
    after = {normalize_heading(s.heading): s for s in parse_markdown(human).sections}
    sections = {
        strip_heading_number(sec.heading): edit_ratio(sec.body, after[key].body) if key in after else 1.0
        for key, sec in before.items()
    }
    added = [strip_heading_number(s.heading) for key, s in after.items() if key not in before]
    return sections, added


def find_pairs(db: sqlite3.Connection, doc_type: str | None) -> list[Pair]:
    sql = (
        "select v.spec_id, s.title, s.doc_type, v.version_no, v.source, v.content "
        "from spec_versions v join specs s on s.id = v.spec_id"
    )
    params: list[str] = []
    if doc_type:
        sql += " where s.doc_type = ?"
        params.append(doc_type)
    by_spec: dict[int, list[tuple]] = defaultdict(list)
    for row in db.execute(sql + " order by v.spec_id, v.version_no", params):
        by_spec[row[0]].append(row)

    pairs: list[Pair] = []
    for rows in by_spec.values():
        ai = None
        human = None
        for row in [*rows, None]:  # None：最後收尾
            if row is None or row[4] == "agent":
                if ai is not None and human is not None:
                    sections, added = compare(ai[5], human[5])
                    pairs.append(
                        Pair(
                            ai[0],
                            ai[1],
                            ai[2],
                            ai[3],
                            human[3],
                            sections,
                            added,
                            edit_ratio(ai[5], human[5], False),
                        )
                    )
                ai, human = row, None
            elif row[4] == "manual" and ai is not None:
                human = row
    return pairs


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--doc-type", help="只看這種文件（範本 key）")
    parser.add_argument("--top", type=int, default=10, help="列出改最多的幾個章節")
    args = parser.parse_args()

    db = sqlite3.connect(f"file:{args.db.as_posix()}?mode=ro", uri=True)
    ai_versions = db.execute("select count(*) from spec_versions where source = 'agent'").fetchone()[0]
    pairs = find_pairs(db, args.doc_type)
    print(f"AI 版本 {ai_versions} 個，後面有人工修改的 {len(pairs)} 個")
    if not pairs:
        print("還沒有可以比較的資料：AI 寫完後由人修改、存檔，就會出現在這裡。")
        return 0

    print("\n每一對：")
    print("  整份修改  大改的章節  人新增的章節  文件")
    for p in pairs:
        heavy = sum(1 for r in p.sections.values() if r >= HEAVY)
        print(
            f"  {p.whole:7.0%} {heavy:6} / {len(p.sections):<4} {len(p.added):8}      "
            f"{p.title}（{p.doc_type}，v{p.ai_no} → v{p.human_no}）"
        )

    by_type: dict[str, list[Pair]] = defaultdict(list)
    for p in pairs:
        by_type[p.doc_type].append(p)
    print("\n依文件類型：")
    print("  份數  平均整份修改  章節平均修改  文件類型")
    for doc_type, ps in sorted(by_type.items()):
        ratios = [r for p in ps for r in p.sections.values()]
        avg_section = sum(ratios) / len(ratios) if ratios else 0.0
        print(f"  {len(ps):4} {sum(p.whole for p in ps) / len(ps):12.0%} {avg_section:12.0%}  {doc_type}")

    heading_stats: dict[tuple[str, str], list[float]] = defaultdict(list)
    for p in pairs:
        for heading, r in p.sections.items():
            heading_stats[(p.doc_type, heading)].append(r)
    ranked = sorted(heading_stats.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))
    print(f"\n平均修改最多的章節（前 {args.top} 個，改了 {HEAVY:.0%} 以上算大改）：")
    print("  平均修改  大改次數  文件類型 / 章節")
    for (doc_type, heading), rs in ranked[: args.top]:
        heavy = sum(1 for r in rs if r >= HEAVY)
        print(f"  {sum(rs) / len(rs):7.0%} {heavy:5} / {len(rs):<4} {doc_type} / {heading}")

    added: dict[tuple[str, str], int] = defaultdict(int)
    for p in pairs:
        for heading in p.added:
            added[(p.doc_type, heading)] += 1
    if added:
        print("\n人另外新增的章節（AI 漏寫的）：")
        for (doc_type, heading), n in sorted(added.items(), key=lambda kv: -kv[1])[: args.top]:
            print(f"  {n:3} 次  {doc_type} / {heading}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
