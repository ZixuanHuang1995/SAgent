"""從一份 Spec 抽出名詞，給使用者確認後加進專案詞彙表。

同一個專案的文件各自撰寫，狀態、角色的名稱容易對不上（功能規格書寫「已派工」，測試計畫寫「維修中」）。
功能規格書這類「定義來源」的文件寫好、人看過之後，把裡面定義的名詞放進專案詞彙表，之後寫測試計畫、
資料庫設計時 Planner、Writer、Reviewer 都會照這些名稱寫。

只用程式抽（不呼叫模型，幾毫秒就好），抽的是文件裡明確定義的地方：
- 名詞定義表（第一欄標題是「名詞」「名稱」「術語」）
- 角色表（「角色」）、狀態表（「狀態」）、實體表（「實體」，英文代號改用說明開頭的中文名稱）
- Mermaid 狀態圖裡的狀態名稱
- 內文寫「狀態為「已提交」」「狀態改為處理中」的狀態名稱（規格文件常這樣寫，沒有另外列狀態表）
- 研究計劃 C-12：任何表格裡「狀態」「目前狀態」「新狀態」欄的值；「狀態依序為：A、B、C」這種列舉；
  提到「狀態」的行裡的轉換「A → B」「從 A 改為 B」

不會自動寫入：跟專案詞彙表比對後分成「新的」和「說明不一樣」，由使用者勾選後才更新。
從文件自動寫進記憶，等於讓一份寫錯的文件影響之後的所有文件，所以一定要人確認。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from ..agents.planner import parse_terms
from .diagrams import extract_diagrams, mermaid_kind
from .markdown_doc import iter_lines_with_fence_state

# 表格第一欄的標題 → 名詞的種類（None：看其他表格決定）
_HEADER_KINDS: dict[str, str | None] = {
    "名詞": None,
    "名稱": None,
    "術語": None,
    "詞彙": None,
    "用語": None,
    "角色": "角色",
    "使用者角色": "角色",
    "狀態": "狀態",
    "狀態名稱": "狀態",
    "實體": "資料",
}
_KIND_ORDER = {"角色": 0, "狀態": 1, "資料": 2, "其他": 3}
_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}")
_STATE_DECL_RE = re.compile(r'^\s*state\s+"([^"]+)"\s+as\s+(\S+)')
_STATE_EDGE_RE = re.compile(r"^\s*(\S+)\s*-->\s*([^:\s]+)")
_IDENT_RE = re.compile(r"^[a-z0-9_]+$")  # 資料表、欄位的英文代號，不算名詞
_CODE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_NOTE_HEAD_RE = re.compile(r"^([^，,；;。（(\s]{1,12})[，,；;。]\s*(.*)$")
# 內文裡的狀態：引號裡的都算；沒有引號的只收 2～4 個字，避免把一整句當成狀態
_STATE_QUOTED_RE = re.compile(r"狀態(?:為|是|改為|改成|變成|變為)「([^」\s]{2,10})」")
_STATE_BARE_RE = re.compile(r"狀態(?:改為|改成|變成|變為)([^\s，。；、「」：:（(]{2,4})(?=[，。；、\s]|$)")
# 研究計劃 C-12：只抽狀態表和「狀態為「…」」時，內文列舉的狀態（「狀態依序為：新建、處理中、已結案」）
# 抽不到，後面的文件寫「已結案」就被當成新名詞
_CJK = "一-鿿"
# 表格裡標題是這些的欄位，值都是狀態（不限第一欄，例如狀態轉換表的「目前狀態」「新狀態」）
_STATE_COLUMNS = {"狀態", "狀態名稱", "目前狀態", "原狀態", "新狀態", "下一個狀態", "變更前狀態", "變更後狀態"}
# 「狀態依序為：A、B、C」「狀態：A、B」
_STATE_LIST_RE = re.compile(r"狀態[^：:。\n|]{0,6}[：:]\s*([^。；;\n|]+)")
# 「依「A、B、C」的順序管理狀態」
_STATE_QUOTED_LIST_RE = re.compile(r"「([^」]{2,80}、[^」]{2,80})」[^。\n]{0,10}狀態")
# 狀態轉換「A → B → C」「從 A 改為 B」只看提到「狀態」的行。左邊的名稱前面要是標點、空白或「由／從」，免得連前面的字一起抓
_STATE_ARROW_RE = re.compile(
    rf"(?:^|(?<=[^{_CJK}])|(?<=[由從]))「?([{_CJK}]{{2,6}})」?\s*(?:→|->|⇒)\s*「?([{_CJK}]{{2,6}})」?(?=[^{_CJK}]|$)"
)
# 「從 A 改為 B」：有引號的 2～10 個字；沒有引號的 2～4 個字，後面要接標點
_STATE_FROM_TO_RE = re.compile(
    rf"從\s*(?:「([^」\s]{{2,10}})」|([{_CJK}]{{2,4}}))\s*(?:改為|改成|變成|變為|轉為)\s*"
    rf"(?:「([^」\s]{{2,10}})」|([{_CJK}]{{2,4}})(?=[^{_CJK}]|$))"
)
_STATE_NAME_RE = re.compile(rf"^[{_CJK}]{{2,6}}$")
_NOT_STATES = {"任何狀態", "所有狀態", "其他狀態", "不變", "同上", "原狀態", "新狀態"}


@dataclass
class Suggestion:
    name: str
    kind: str
    note: str
    # new：專案詞彙表沒有；changed：名稱一樣，種類或說明不一樣
    status: str
    current_kind: str = ""
    current_note: str = ""


def _cells(line: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [re.sub(r"[*`]", "", p).replace("\\|", "|").strip() for p in parts]


def _clean_name(cell: str) -> str:
    name = re.sub(r"[（(][^）)]*[）)]$", "", cell).strip()  # 「employee（員工）」這類只留前面
    return name if name else cell.strip()


def _tables(markdown: str) -> list[tuple[list[str], list[list[str]]]]:
    """文件裡的表格（程式碼區塊裡的不算）：[(每一欄的標題, 每一列的格子)]。"""
    lines = [(ln, in_fence) for ln, in_fence in iter_lines_with_fence_state(markdown)]
    tables = []
    i = 0
    while i < len(lines) - 1:
        line, fenced = lines[i]
        if not fenced and line.strip().startswith("|") and _SEP_RE.match(lines[i + 1][0]):
            header = _cells(line)
            rows = []
            i += 2
            while i < len(lines) and not lines[i][1] and lines[i][0].strip().startswith("|"):
                rows.append(_cells(lines[i][0]))
                i += 1
            tables.append((header, rows))
            continue
        i += 1
    return tables


def _states_from_diagrams(markdown: str) -> list[str]:
    names: list[str] = []
    for block in extract_diagrams(markdown):
        if block.lang != "mermaid" or not mermaid_kind(block.code).startswith("stateDiagram"):
            continue
        alias: dict[str, str] = {}
        for line in block.code.splitlines():
            decl = _STATE_DECL_RE.match(line)
            if decl:
                alias[decl.group(2)] = decl.group(1)
                continue
            edge = _STATE_EDGE_RE.match(line)
            if edge:
                for node in edge.groups():
                    if node != "[*]":
                        names.append(alias.get(node, node))
    return names


def _state_names(text: str) -> list[str]:
    """一格或一串列舉（「新建、處理中 / 已結案」）裡的狀態名稱：2～6 個中文字，去掉引號和括號說明。"""
    names = []
    for part in re.split(r"[、，,/／;；]|→|->|⇒|\s或\s|或", text):
        name = _clean_name(part.strip().strip("「」『』\"'*`").strip())
        if _STATE_NAME_RE.match(name) and name not in _NOT_STATES:
            names.append(name)
    return names


def _states_from_text(markdown: str) -> list[str]:
    names: list[str] = []
    for line, fenced in iter_lines_with_fence_state(markdown):
        if fenced:
            continue
        names += _STATE_QUOTED_RE.findall(line) + _STATE_BARE_RE.findall(line)
        for m in _STATE_LIST_RE.finditer(line):
            names += _state_names(m.group(1))
        for m in _STATE_QUOTED_LIST_RE.finditer(line):
            names += _state_names(m.group(1))
        if "狀態" in line:
            # 「從一對一改成一對多」這種不是狀態，所以轉換的寫法只看提到「狀態」的行
            for m in _STATE_FROM_TO_RE.finditer(line):
                names += [n for n in m.groups() if n and n not in _NOT_STATES]
            # 重疊比對，才抓得到「A → B → C」中間的 B
            for i in range(len(line)):
                if m := _STATE_ARROW_RE.match(line, i):
                    names += [n for n in m.groups() if n not in _NOT_STATES]
    return names


def _states_from_columns(tables: list[tuple[list[str], list[list[str]]]]) -> list[str]:
    """表格裡標題是「狀態」「目前狀態」「新狀態」的欄位（不限第一欄）的值。"""
    names: list[str] = []
    for header, rows in tables:
        cols = [i for i, h in enumerate(header) if re.sub(r"\s", "", h) in _STATE_COLUMNS]
        for row in rows:
            for i in cols:
                if i < len(row):
                    names += _state_names(row[i])
    return names


def _entity_name(name: str, note: str) -> tuple[str, str]:
    """實體表常寫「REPAIR_REQUEST | 報修單，是整個系統的核心」：名詞用說明開頭的中文名稱。"""
    if _CODE_NAME_RE.match(name) and (m := _NOTE_HEAD_RE.match(note)):
        return m.group(1), m.group(2)
    return name, note


def extract_terms(markdown: str) -> list[dict[str, str]]:
    """文件裡明確定義的名詞：[{name, kind, note}]，角色、狀態、資料、其他依序排。"""
    found: dict[str, dict[str, str]] = {}
    kinds: dict[str, str] = {}

    def add(name: str, kind: str | None, note: str) -> None:
        name = _clean_name(name)
        if not name or name in ("—", "-") or len(name) > 20 or _IDENT_RE.match(name):
            return
        term = found.setdefault(name, {"name": name, "kind": "", "note": ""})
        if note and not term["note"]:
            term["note"] = note[:100]
        if kind:
            kinds.setdefault(name, kind)

    tables = _tables(markdown)
    for header, rows in tables:
        key = re.sub(r"\s", "", header[0] if header else "")
        if key not in _HEADER_KINDS:
            continue
        for row in rows:
            if not row:
                continue
            name, note = _clean_name(row[0]), row[1] if len(row) > 1 else ""
            if _HEADER_KINDS[key] == "資料":
                name, note = _entity_name(name, note)
            if _CODE_NAME_RE.match(name) and _HEADER_KINDS[key] == "資料":
                continue  # 只有英文代號、說明也沒有中文名稱
            add(name, _HEADER_KINDS[key], note)
    for state in _states_from_diagrams(markdown) + _states_from_text(markdown) + _states_from_columns(tables):
        add(state, "狀態", "")
    terms = [{**t, "kind": kinds.get(t["name"], "其他")} for t in found.values()]
    return sorted(terms, key=lambda t: _KIND_ORDER.get(t["kind"], 9))


def _same(a: str, b: str) -> bool:
    return re.sub(r"[\s，。、；：,.;:]", "", a) == re.sub(r"[\s，。、；：,.;:]", "", b)


def suggestions(glossary: list[dict], markdown: str) -> list[Suggestion]:
    """跟專案詞彙表比對：新的名詞，以及名稱一樣但種類或說明不一樣的（要使用者決定用哪一個）。"""
    current = {t.name: t for t in parse_terms(glossary, limit=1000)}
    out: list[Suggestion] = []
    for term in extract_terms(markdown):
        old = current.get(term["name"])
        if old is None:
            out.append(Suggestion(term["name"], term["kind"], term["note"], "new"))
            continue
        kind_differs = term["kind"] != "其他" and term["kind"] != old.kind
        note_differs = bool(term["note"]) and not _same(term["note"], old.note)
        if kind_differs or note_differs:
            # 文件沒寫明種類（只在名詞定義表裡）或沒有說明時，保留詞彙表原本的，不要把「角色」蓋成「其他」
            kind = term["kind"] if term["kind"] != "其他" else old.kind
            out.append(
                Suggestion(term["name"], kind, term["note"] or old.note, "changed", old.kind, old.note)
            )
    return out


def suggestions_dict(glossary: list[dict], markdown: str) -> list[dict]:
    return [asdict(s) for s in suggestions(glossary, markdown)]
