"""把模型輸出裡偶爾混入的簡體字改成繁體。

不能整段用 OpenCC 轉換：已經是繁體的文字會被改壞（例如「干擾」變「幹擾」、「通過」變「透過」）。
所以只處理「只有簡體才會用的字」（例如「汇」「软」），並且用前後幾個字一起轉換，
讓 OpenCC 依詞語挑對的繁體字（例如「汇出」→「匯出」、「词汇」→「詞彙」），其他字完全不動。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_WINDOW = 3


@lru_cache(maxsize=1)
def _tables():
    try:
        import opencc
        from opencc import OpenCC
    except ImportError:  # 沒裝套件就不轉換
        return None
    path = Path(opencc.__file__).parent / "dictionary" / "STCharacters.txt"
    simplified_only: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        simp, _, targets = line.partition("\t")
        candidates = targets.split()
        # 對應的繁體字裡沒有它自己，表示這個字只在簡體中文使用
        if simp and candidates and simp not in candidates:
            simplified_only[simp] = candidates[0]
    return OpenCC("s2t"), simplified_only


def to_traditional(text: str) -> str:
    tables = _tables()
    if tables is None or not text:
        return text
    converter, simplified_only = tables
    if not any(ch in simplified_only for ch in text):
        return text
    out = list(text)
    for i, ch in enumerate(text):
        if ch not in simplified_only:
            continue
        lo, hi = max(0, i - _WINDOW), min(len(text), i + _WINDOW + 1)
        window = text[lo:hi]
        converted = converter.convert(window)
        if len(converted) != len(window):
            out[i] = simplified_only[ch]
            continue
        # 這個字和緊鄰的字一起換（例如「数据」的「据」兩邊通用，跟著「数」一起換成「據」）
        for k in (i - 1, i, i + 1):
            if lo <= k < hi and _is_cjk(text[k]):
                out[k] = converted[k - lo]
    return "".join(out)


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿"
