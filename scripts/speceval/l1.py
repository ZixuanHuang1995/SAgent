"""SpecEval L1：拿需求原文比對文件（不跑模型）。

四個指標：
- coverage：需求裡的「事實」（帶單位的數字、三位數以上的數字、「」裡的名詞）文件寫到幾成
- invented：文件裡帶單位的數字，需求裡沒有出現過的（可能是編的）
- copy_ratio：文件有幾成是整段照抄需求原文（連續 12 個字以上一樣）
- hollow：內容空洞的章節（不算標了「待確認」「假設」的行，剩下的中文字不到 30 個）

通過（passed）= coverage ≥ 0.9、copy_ratio < 0.3、沒有空洞章節。invented 不列入自動判定，只列給 L2、L3：
示範文件的實驗裡，「需求沒有的數字」大多是合理的（換算出來的數字、測試的邊界值、前一份文件寫過的事實、
文件自己的統計），直接判失敗會誤判三成的好文件。
L1 只看得出「有沒有寫到、是不是照抄、是不是空洞」，看不出寫得對不對；邏輯和可讀性要靠 L2、L3。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

_CJK = "[一-鿿]"
_FENCE_RE = re.compile(r"```.*?```", re.S)
_ID_RE = re.compile(r"\b[A-Z]{1,4}-\d{2,3}\b")
_MARK_RE = re.compile(r"[（(](假設|待確認)[）)]")
_QUOTED_RE = re.compile(r"「([^」]{2,20})」")
_UNITS = r"個工作天|工作天|個日曆天|日曆天|個月|小時|分鐘|天|週|年|次|張|件|筆|人|位|間|台|MB|GB|KB|TB|ms|%|秒|字元|字"
# 跟 reviewer.py 的 _FACT_RE 同一個想法，單位多收幾種
_FACT_RE = re.compile(rf"(?<![\d/.:A-Za-z-])(\d+(?:\.\d+)?)\s*({_UNITS})?(?![\d/:])")
_OPEN_HEADING = "待確認事項"

PASS_COVERAGE = 0.9
PASS_COPY = 0.3


@dataclass
class L1Result:
    facts: int = 0
    covered: int = 0
    missing: list[str] = field(default_factory=list)
    invented: list[str] = field(default_factory=list)
    copy_ratio: float = 0.0
    sections: int = 0
    hollow: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.covered / self.facts if self.facts else 1.0

    @property
    def passed(self) -> bool:
        return self.coverage >= PASS_COVERAGE and self.copy_ratio < PASS_COPY and not self.hollow

    @property
    def passed_strict(self) -> bool:
        """連「需求沒有的數字」也算失敗的舊規則，留著比較誤判率。"""
        return self.passed and not self.invented

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(
            coverage=round(self.coverage, 3),
            copy_ratio=round(self.copy_ratio, 3),
            passed=self.passed,
            passed_strict=self.passed_strict,
        )
        return d


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", _FENCE_RE.sub("", text))


def requirement_facts(requirement: str) -> list[tuple[str, str, str]]:
    """（顯示文字, 數字, 單位）或（「名詞」, 名詞, ""）。"""
    facts: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for m in _FACT_RE.finditer(requirement):
        number, unit = m.group(1), m.group(2) or ""
        if not unit and len(number) < 3:
            continue
        key = number + unit
        if key not in seen:
            seen.add(key)
            facts.append((m.group(0).strip(), number, unit))
    for m in _QUOTED_RE.finditer(requirement):
        term = m.group(1)
        if term not in seen:
            seen.add(term)
            facts.append((f"「{term}」", term, ""))
    return facts


def _has_fact(flat_doc: str, number: str, unit: str) -> bool:
    if not re.fullmatch(r"\d+(?:\.\d+)?", number):
        return number in flat_doc
    if not unit:
        return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])", flat_doc) is not None
    # 數字和單位中間可以夾幾個字（「3 個日曆天」）
    return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])[^\d]{{0,4}}{re.escape(unit[-1:])}", flat_doc) is not None


def invented_numbers(doc: str, requirement: str) -> list[str]:
    """文件裡帶單位的數字，需求裡沒有這個數值。編號（FR-01）、條列序號不算。"""
    known = set(re.findall(r"\d+(?:\.\d+)?", requirement))
    text = _ID_RE.sub("", _FENCE_RE.sub("", doc))
    text = re.sub(r"^\s*(?:\d+[.)、]|#+\s*\d+(?:\.\d+)*)\s*", "", text, flags=re.M)
    out: list[str] = []
    for m in _FACT_RE.finditer(text):
        number, unit = m.group(1), m.group(2) or ""
        if unit and number not in known and number not in ("0", "1"):
            label = f"{number}{unit}"
            if label not in out:
                out.append(label)
    return out


def copy_ratio(doc: str, requirement: str, k: int = 12) -> float:
    """文件（不含空白）有幾成落在「跟需求原文連續 k 個字一樣」的片段裡。"""
    d, r = _flat(doc), _flat(requirement)
    if len(d) < k or len(r) < k:
        return 0.0
    shingles = {r[i : i + k] for i in range(len(r) - k + 1)}
    hit = [False] * len(d)
    for i in range(len(d) - k + 1):
        if d[i : i + k] in shingles:
            for j in range(i, i + k):
                hit[j] = True
    return sum(hit) / len(d)


def split_sections(doc: str) -> list[tuple[str, str]]:
    parts = re.split(r"^## +(.+)$", _FENCE_RE.sub("", doc), flags=re.M)
    return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def hollow_sections(doc: str) -> list[str]:
    out: list[str] = []
    for heading, body in split_sections(doc):
        if _OPEN_HEADING in heading:
            continue
        plain = [ln for ln in body.splitlines() if not (_MARK_RE.search(ln) or "待確認" in ln)]
        cjk = len(re.findall(_CJK, _ID_RE.sub("", "\n".join(plain))))
        if cjk < 30:
            out.append(heading)
    return out


def score(doc: str, requirement: str, context: str = "") -> L1Result:
    """context：同一個專案其他文件、專案詞彙表等，只用來判斷數字是不是編的。"""
    flat = _flat(doc)
    facts = requirement_facts(requirement)
    res = L1Result(facts=len(facts))
    for label, number, unit in facts:
        if _has_fact(flat, number, unit):
            res.covered += 1
        else:
            res.missing.append(label)
    res.invented = invented_numbers(doc, requirement + "\n" + context)
    res.copy_ratio = copy_ratio(doc, requirement)
    res.sections = len(split_sections(doc))
    res.hollow = hollow_sections(doc)
    return res
