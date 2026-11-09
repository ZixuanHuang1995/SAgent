"""第 7 堂（記憶）的稽核：每一步的提示詞裡，各種記憶占了多少；同一個專案的文件，名詞前後一不一致。

1. 提示詞組成：用腳本模型跑 oracle 文件的 AI 流程，記下每次呼叫的提示詞，依「## 標題」切塊，
   算各塊的字元數。看得出工作記憶（這一節的目標）、語意記憶（名詞表、詞彙表）、
   前面章節的編號、需求原文各占多少，以及哪一步最容易超過上下文長度。
   這裡算的是字元數；真的 token 數看 analyze_runs.py（真模型的每一步都記了 prompt_tokens 和 context_length）。
2. 跨文件一致性：每個專案以功能規格書抽出的名詞當詞彙表（services/glossary.py 的 extract_terms），
   跟同專案其他文件比對，列出詞彙表沒有的角色、狀態，以及名稱一樣但說明不同的名詞。
   SAgent 的專案詞彙表要使用者手動確認才更新；這裡看「如果都沒更新」會有多少不一致。

用法（在專案根目錄）：
    uv run --project backend python scripts/speceval/memory_audit.py --out speceval-out/memory.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))

from check_demo_content import doc_type_of, phase_of, run_options, templates  # noqa: E402
from controls import make_variant, requirement_of  # noqa: E402
from demo_llm import ContentBook, ScriptedLLM  # noqa: E402
from demo_scenarios import SCENARIOS  # noqa: E402

from sagent.agents.base import ListEmitter  # noqa: E402
from sagent.agents.pipeline import SpecPipeline  # noqa: E402
from sagent.agents.types import PipelineOptions, RunInput  # noqa: E402
from sagent.services.glossary import extract_terms, suggestions  # noqa: E402

KEYS = ("idea", "feature", "architecture", "database", "change")


class RecordingLLM(ScriptedLLM):
    calls: list[dict]

    async def chat(self, messages, **kw):
        full = "\n".join(m["content"] for m in messages)
        blocks: dict[str, int] = defaultdict(int)
        current = "（開頭）"
        for line in full.splitlines():
            m = re.match(r"^#{1,3} (.+)$", line)
            if m:
                current = re.sub(r"[：:].*$", "", m.group(1)).strip()[:20]
            blocks[current] += len(line) + 1
        purpose = kw.get("purpose", "")
        role, _, step = purpose.partition(":")
        kind = role if role in ("planner", "reviewer") else re.sub(r"[:\d].*$", "", step) or role
        self.calls.append({"purpose": purpose, "kind": kind, "chars": len(full), "blocks": dict(blocks)})
        return await super().chat(messages, **kw)


async def run_oracle(sc, key, tpls, root: Path, calls: list[dict]) -> str:
    book = ContentBook(SCRIPTS / "demo_content")
    doc_type = doc_type_of(sc, key)
    tpl = tpls[doc_type]
    requirement = requirement_of(sc, key)
    variant = make_variant("oracle", book.load(sc.code, key), requirement, tpl)
    (root / sc.code).mkdir(parents=True, exist_ok=True)
    (root / sc.code / f"{key}.json").write_text(json.dumps(variant, ensure_ascii=False), encoding="utf-8")
    llm = RecordingLLM(ContentBook(root), sc.code, key, model="script", delay=0)
    llm.calls = calls
    opts = run_options(sc, doc_type)
    inp = RunInput(
        mode="draft",
        project_name=sc.name,
        project_description=sc.description,
        phase_name=phase_of(sc, key),
        spec_title=sc.titles[key],
        template=tpl,
        requirement=requirement,
        existing_content="",
        options=PipelineOptions(max_revisions=0, diagrams=tpl.allow_diagrams, max_diagrams=opts.get("max_diagrams")),
    )
    result = await SpecPipeline(llm, ListEmitter()).run(inp)
    return result.markdown


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("speceval-out/memory.json"))
    args = ap.parse_args()
    tpls = templates()
    calls: list[dict] = []
    docs: dict[str, dict[str, str]] = defaultdict(dict)
    with tempfile.TemporaryDirectory() as tmp:
        for sc in SCENARIOS:
            for key in KEYS:
                docs[sc.code][key] = await run_oracle(sc, key, tpls, Path(tmp), calls)

    # 1. 提示詞組成
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for c in calls:
        by_kind[c["kind"]].append(c)
    composition = {}
    for kind, cs in by_kind.items():
        totals: dict[str, int] = defaultdict(int)
        for c in cs:
            for b, n in c["blocks"].items():
                totals[b] += n
        all_chars = sum(totals.values())
        top = sorted(totals.items(), key=lambda kv: -kv[1])[:8]
        composition[kind] = {
            "calls": len(cs),
            "chars_median": statistics.median(c["chars"] for c in cs),
            "chars_max": max(c["chars"] for c in cs),
            "share": {b: round(n / all_chars, 3) for b, n in top},
        }
        print(f"{kind:10} 呼叫 {len(cs):3} 次，字元中位數 {composition[kind]['chars_median']:7.0f}，最大 "
              f"{composition[kind]['chars_max']:6}；占比：" + "、".join(f"{b} {v:.0%}" for b, v in composition[kind]["share"].items()))

    # 2. 跨文件一致性
    consistency = {}
    for code, ds in docs.items():
        glossary = extract_terms(ds["feature"])
        names = {t["name"] for t in glossary}
        row = {"glossary_terms": len(glossary)}
        for key in KEYS:
            if key == "feature":
                continue
            sugg = suggestions(glossary, ds[key])
            new_roles_states = [s.name for s in sugg if s.status == "new" and s.kind in ("角色", "狀態")]
            changed = [s.name for s in sugg if s.status == "changed"]
            row[key] = {"new_roles_states": new_roles_states, "changed": changed,
                        "terms": len(extract_terms(ds[key])),
                        "shared": len(names & {t["name"] for t in extract_terms(ds[key])})}
        consistency[code] = row
        print(code, json.dumps(row, ensure_ascii=False)[:600])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"composition": composition, "consistency": consistency}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"結果：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
