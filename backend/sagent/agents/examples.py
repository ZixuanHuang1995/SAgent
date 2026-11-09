"""人改過的章節當 few-shot 範例（研究計劃 C-19，實驗，預設關閉）。

範例由人挑（從 scripts/speceval/export_traces.py 匯出的人改過的版本裡選），存成
<資料資料夾>/examples/<文件類型>/<章節名稱>.md，例如 data/examples/functional_spec/功能需求.md。
一個檔案可以放 1～2 段範例，用一行 <!-- 範例分隔 --> 隔開；每段最多 MAX_CHARS 個字。

範例放在 Writer 章節提示詞的最後面（「現在要寫的章節」之後），前段照樣跟其他章節共用 Ollama 的快取。
要改成預設開啟，得先用 SpecEval full-12 比較：可交付率的配對差 95% 信賴區間 > 0、時間增加 < 20%。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..services.markdown_doc import strip_heading_number

SEPARATOR = "<!-- 範例分隔 -->"
MAX_EXAMPLES = 2
MAX_CHARS = 1500
_BAD_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def example_file_name(heading: str) -> str:
    """章節名稱 → 檔名：去掉編號（「4. 功能需求」→「功能需求.md」），Windows 不能用的字換成底線。"""
    return _BAD_CHARS_RE.sub("_", strip_heading_number(heading).strip()) + ".md"


def load_examples(root: Path | None, doc_type: str, headings: list[str]) -> dict[str, list[str]]:
    """這種文件每一節的範例：{章節名稱（去掉編號）: [範例, …]}。沒有資料夾或沒有範例檔時回傳 {}。"""
    if root is None:
        return {}
    folder = root / doc_type
    if not folder.is_dir():
        return {}
    out: dict[str, list[str]] = {}
    for heading in headings:
        path = folder / example_file_name(heading)
        if not path.is_file():
            continue
        parts = [p.strip() for p in path.read_text(encoding="utf-8").split(SEPARATOR)]
        examples = [p[:MAX_CHARS] for p in parts if p][:MAX_EXAMPLES]
        if examples:
            out[example_file_name(heading)[:-3]] = examples
    return out


def examples_for(examples: dict[str, list[str]], heading: str) -> list[str]:
    return examples.get(example_file_name(heading)[:-3], [])
