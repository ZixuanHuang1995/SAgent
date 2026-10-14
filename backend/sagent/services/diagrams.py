"""Markdown 裡的圖表區塊（Mermaid / PlantUML）：抓出來、檢查語法、自動修正常見錯誤。

這裡只做「看得出來的」語法檢查，不是完整的解析器。
真正的渲染結果在前端（Mermaid）或 PlantUML 伺服器確認。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

DIAGRAM_LANGS = {"mermaid": "mermaid", "plantuml": "plantuml", "puml": "plantuml", "uml": "plantuml"}

MERMAID_KEYWORDS = {
    "flowchart",
    "graph",
    "sequenceDiagram",
    "classDiagram",
    "classDiagram-v2",
    "stateDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "journey",
    "gantt",
    "pie",
    "quadrantChart",
    "requirementDiagram",
    "gitGraph",
    "C4Context",
    "C4Container",
    "C4Component",
    "C4Dynamic",
    "C4Deployment",
    "mindmap",
    "timeline",
    "zenuml",
    "sankey-beta",
    "sankey",
    "xychart-beta",
    "xychart",
    "block-beta",
    "block",
    "packet-beta",
    "packet",
    "kanban",
    "architecture-beta",
    "radar-beta",
    "treemap-beta",
    "treemap",
    "usecase-beta",
}

_OPEN_FENCE_RE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})\s*([^\s`]*)")


@dataclass
class DiagramBlock:
    lang: str  # mermaid | plantuml
    code: str
    start: int  # 開頭 ``` 那一行的行號（0 起算）
    end: int  # 區塊最後一行的行號（通常是結尾的 ```）
    info: str  # ``` 後面原本寫的語言名稱
    fence: str = "```"
    closed: bool = True  # False 表示文件結束前都沒有結尾的 ```


def _is_closing(line: str, fence: str) -> bool:
    s = line.strip()
    return bool(s) and s[0] == fence[0] and set(s) == {fence[0]} and len(s) >= len(fence)


def extract_diagrams(markdown: str) -> list[DiagramBlock]:
    lines = markdown.splitlines()
    blocks: list[DiagramBlock] = []
    i = 0
    while i < len(lines):
        m = _OPEN_FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        fence, info = m.group(2), m.group(3).lower()
        j = i + 1
        while j < len(lines) and not _is_closing(lines[j], fence):
            j += 1
        if info in DIAGRAM_LANGS:
            closed = j < len(lines)
            blocks.append(
                DiagramBlock(
                    lang=DIAGRAM_LANGS[info],
                    code="\n".join(lines[i + 1 : j]),
                    start=i,
                    end=j if closed else len(lines) - 1,
                    info=info,
                    fence=fence,
                    closed=closed,
                )
            )
        i = j + 1
    return blocks


def transform_diagrams(markdown: str, fn: Callable[[int, DiagramBlock], str | None]) -> str:
    """把每個圖表區塊交給 fn 處理。fn 回傳新的程式碼；回傳 None 表示不改。"""
    blocks = extract_diagrams(markdown)
    if not blocks:
        return markdown
    lines = markdown.splitlines()
    out: list[str] = []
    cursor = 0
    for idx, block in enumerate(blocks):
        new_code = fn(idx, block)
        if new_code is None:
            continue
        out.extend(lines[cursor : block.start + 1])
        out.extend(new_code.strip("\n").splitlines())
        out.append(lines[block.end] if block.closed else block.fence)
        cursor = block.end + 1
    out.extend(lines[cursor:])
    result = "\n".join(out)
    if markdown.endswith("\n"):
        result += "\n"
    return result


def fence(lang: str, code: str) -> str:
    return f"```{lang}\n{code.strip()}\n```"


# ---------------------------------------------------------------------------
# 從模型回覆中取出程式碼
# ---------------------------------------------------------------------------

_ANY_FENCE_RE = re.compile(r"(`{3,}|~{3,})[ \t]*([^\s`]*)[^\n]*\n(.*?)\n[ \t]*\1", re.S)
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def strip_think(text: str) -> str:
    text = _THINK_RE.sub("", text)
    if "</think>" in text:  # 只有結尾標記時，前面都是思考內容
        text = text.split("</think>", 1)[1]
    return text.strip()


def extract_code(text: str, lang: str) -> str:
    """模型常會多講話或包 ```，這裡把真正的圖表程式碼挑出來。"""
    text = strip_think(text)
    fences = _ANY_FENCE_RE.findall(text)
    if fences:
        wanted = {k for k, v in DIAGRAM_LANGS.items() if v == lang}
        for _, info, body in fences:
            if info.lower() in wanted:
                return body.strip()
        return fences[0][2].strip()
    if lang == "plantuml":
        m = re.search(r"@start(\w+).*?@end\1", text, re.S)
        if m:
            return m.group(0).strip()
    else:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            word = line.strip().split(" ")[0] if line.strip() else ""
            if word in MERMAID_KEYWORDS:
                return "\n".join(lines[i:]).strip()
    return text.strip().strip("`").strip()


# ---------------------------------------------------------------------------
# Mermaid
# ---------------------------------------------------------------------------


def _mermaid_content_lines(code: str) -> list[str]:
    """去掉 frontmatter、%% 註解和空行，留下真正的語法行。"""
    lines = code.splitlines()
    if lines and lines[0].strip() == "---":
        for k in range(1, len(lines)):
            if lines[k].strip() == "---":
                lines = lines[k + 1 :]
                break
    return [ln for ln in lines if ln.strip() and not ln.strip().startswith("%%")]


def mermaid_kind(code: str) -> str:
    lines = _mermaid_content_lines(code)
    if not lines:
        return ""
    return lines[0].strip().split()[0].rstrip(";")


def _strip_quoted(s: str) -> str:
    return re.sub(r'"[^"]*"', '""', s)


_BRACKET_PAIRS = {"[": "]", "(": ")", "{": "}"}


def _brackets_balanced(s: str) -> bool:
    stack: list[str] = []
    for ch in _strip_quoted(s):
        if ch in _BRACKET_PAIRS:
            stack.append(_BRACKET_PAIRS[ch])
        elif ch in _BRACKET_PAIRS.values():
            if not stack or stack.pop() != ch:
                return False
    return not stack


_FLOW_SKIP_PREFIXES = (
    "style ",
    "classDef ",
    "class ",
    "click ",
    "linkStyle ",
    "direction ",
    "accTitle",
    "accDescr",
)


def _lint_flowchart(body: list[str]) -> list[str]:
    errors: list[str] = []
    opened = 0
    for no, raw in enumerate(body, start=2):
        line = raw.strip().rstrip(";")
        if line.startswith(_FLOW_SKIP_PREFIXES) or "@{" in line:
            continue
        if line.startswith("subgraph"):
            opened += 1
            rest = line[len("subgraph") :]
            if _quote_flowchart_line(rest) != rest:
                errors.append(f'第 {no} 行：子圖標題有括號等符號時，要寫成 subgraph 代號["標題"]')
            continue
        if line == "end":
            opened -= 1
            if opened < 0:
                errors.append(f"第 {no} 行：多了一個 end")
                opened = 0
            continue
        if not _brackets_balanced(line):
            errors.append(f'第 {no} 行：括號沒有成對，節點文字有括號時要用雙引號包起來，例如 A["文字(說明)"]')
        elif _quote_flowchart_line(line) != line:
            errors.append(
                f'第 {no} 行：節點或連線文字有括號、引號或 @ 等符號時，要用雙引號包起來，例如 A["文字(說明)"]'
            )
        if _strip_quoted(line).count("|") % 2:
            errors.append(f"第 {no} 行：連線文字的 | 沒有成對")
        if re.search(r"(-->|---|==>|-\.->)\s*end\b|\bend\s*(-->|---|==>)", _strip_quoted(line)):
            errors.append(f"第 {no} 行：不能用 end 當節點名稱")
    if opened > 0:
        errors.append("subgraph 和 end 數量不一致")
    return errors


_SEQ_ARROW_RE = re.compile(r"(<<-->>|<<->>|-->>|->>|-->|->|--x|-x|--\)|-\))")
_SEQ_KEYWORDS = (
    "participant",
    "actor",
    "note",
    "loop",
    "alt",
    "else",
    "opt",
    "par",
    "and",
    "end",
    "rect",
    "activate",
    "deactivate",
    "autonumber",
    "title",
    "box",
    "critical",
    "option",
    "break",
    "create",
    "destroy",
    "link",
    "links",
    "properties",
    "details",
    "acctitle",
    "accdescr",
)
_SEQ_BLOCK_OPEN = ("loop", "alt", "opt", "par", "rect", "critical", "break", "box")


def _lint_sequence(body: list[str]) -> list[str]:
    errors: list[str] = []
    depth = 0
    for no, raw in enumerate(body, start=2):
        line = raw.strip()
        word = re.split(r"[\s:]", line, maxsplit=1)[0].lower()  # Mermaid 循序圖關鍵字不分大小寫
        if word in _SEQ_BLOCK_OPEN:
            depth += 1
            continue
        if word == "end":
            depth -= 1
            if depth < 0:
                errors.append(f"第 {no} 行：多了一個 end")
                depth = 0
            continue
        if word in _SEQ_KEYWORDS:
            continue
        if _SEQ_ARROW_RE.search(line):
            if ":" not in line:
                errors.append(f"第 {no} 行：訊息要寫成「A->>B: 訊息內容」，缺少冒號")
            elif ";" in line.split(":", 1)[1]:
                errors.append(f"第 {no} 行：訊息內容不能有半形分號 ;，請改用全形 ；")
            continue
        errors.append(f"第 {no} 行：看不懂的循序圖語法「{line[:40]}」")
    if depth > 0:
        errors.append("loop/alt/opt 等區塊缺少 end")
    return errors


_ER_REL_RE = re.compile(
    r'^\s*("[^"]+"|[\w\-]+)\s+(\|o|\|\||\}o|\}\|)(--|\.\.)(o\||\|\||o\{|\|\{)\s+("[^"]+"|[\w\-]+)\s*:\s*\S.*$'
)
_ER_ENTITY_OPEN_RE = re.compile(r'^\s*("[^"]+"|[\w\-]+)(\s*\[[^\]]*\])?\s*\{\s*$')
# 屬性：型別 名稱 [PK, FK] ["說明"]。型別和名稱可以是中文，但不能有空白
_ER_ATTR_RE = re.compile(r'^\s*[^\s"{}]+\s+\*?[^\s"{}]+(\s+(PK|FK|UK)(\s*,\s*(PK|FK|UK))*)?(\s+"[^"]*")?\s*$')


def _lint_er(body: list[str]) -> list[str]:
    errors: list[str] = []
    in_entity = False
    for no, raw in enumerate(body, start=2):
        line = raw.strip()
        if in_entity:
            if line == "}":
                in_entity = False
            elif not _ER_ATTR_RE.match(line):
                errors.append(
                    f'第 {no} 行：屬性要寫成「型別 名稱 PK或FK "說明"」，名稱中間不能有空白，說明放最後，例如 string user_id PK "使用者編號"'
                )
            continue
        if _ER_ENTITY_OPEN_RE.match(line):
            in_entity = True
            continue
        if line.startswith(("title", "direction", "accTitle", "accDescr", "style", "classDef", "class ")):
            continue
        # 用符號寫的關聯（||--o{ 這類）要符合格式；Mermaid 也接受「A one to many B : x」這種文字寫法，不檢查
        if re.search(r"[|}{o](--|\.\.)[|}{o]", line) and not _ER_REL_RE.match(line):
            errors.append(f'第 {no} 行：關聯要寫成「CUSTOMER ||--o{{ ORDERS : "下訂"」，最後要有冒號和說明')
    if in_entity:
        errors.append("實體的 { } 沒有成對")
    return errors


# Mermaid 12 原生使用案例圖（usecase-beta）。規則依實際解析器測試整理
_UC_REF = r'([A-Za-z_]\w*|"[^"\n]+")'
_UC_LABEL = r'\("[^"\n]*"\)'
_UC_LINE_RES = [
    re.compile(r"^direction\s+(TB|TD|BT|LR|RL)$"),
    re.compile(rf"^actor\s+{_UC_REF}({_UC_LABEL})?$"),
    re.compile(rf"^systemBoundary\s+{_UC_REF}({_UC_LABEL})?$"),
    re.compile(rf"^{_UC_REF}{_UC_LABEL}$"),
    re.compile(
        rf"^{_UC_REF}({_UC_LABEL})?\s*(-->|--\|>|\.\.>\s*:\s*(include|extend))\s*{_UC_REF}({_UC_LABEL})?$",
        re.I,
    ),
    re.compile(rf'^{_UC_REF}\s*--\s*("[^"\n]*"|[^"\n]+?)\s*-->\s*{_UC_REF}({_UC_LABEL})?$'),
    re.compile(r'^note\s+for\s+\S+\s+"[^"\n]*"$'),
    re.compile(r"^(classDef|class|style|title|accTitle|accDescr)\b"),
]


def _lint_usecase(body: list[str]) -> list[str]:
    errors: list[str] = []
    depth = 0
    for no, raw in enumerate(body, start=2):
        line = raw.strip()
        if line == "end":
            depth -= 1
            if depth < 0:
                errors.append(f"第 {no} 行：多了一個 end")
                depth = 0
            continue
        if line.startswith("systemBoundary"):
            depth += 1
        if any(p.match(line) for p in _UC_LINE_RES):
            continue
        if "<<include>>" in line or "<<extend>>" in line:
            errors.append(
                f"第 {no} 行：包含關係要寫成「UC2 ..>:include UC1」，延伸關係寫成「UC3 ..>:extend UC1」"
            )
        elif re.match(r"^(actor|systemBoundary)\s", line):
            errors.append(
                f'第 {no} 行：代號只能用英文字母和數字，中文名稱放在括號裡，例如 actor U1("使用者")'
            )
        else:
            errors.append(
                f'第 {no} 行：看不懂的使用案例圖語法「{line[:40]}」，使用案例要寫成 UC1("名稱")，連線寫成 U1 --> UC1'
            )
    if depth > 0:
        errors.append("systemBoundary 缺少 end")
    return errors


def _lint_braces(body: list[str]) -> list[str]:
    text = _strip_quoted("\n".join(body))
    if text.count("{") != text.count("}"):
        return ["{ } 沒有成對"]
    return []


def lint_mermaid(code: str) -> list[str]:
    lines = _mermaid_content_lines(code)
    if not lines:
        return ["圖表是空的"]
    if any(ln.strip().startswith(("```", "~~~")) for ln in lines):
        return ["圖表程式碼裡不能有 ``` 符號"]
    if _PUML_START_RE.search(code):
        return ["這是 PlantUML 語法，這裡要用 Mermaid 語法"]
    kind = mermaid_kind(code)
    if kind not in MERMAID_KEYWORDS:
        return [f"第一行要寫圖表類型（例如 flowchart TD），目前是「{lines[0].strip()[:40]}」"]
    body = [ln for ln in lines[1:] if not ln.strip().startswith(("accTitle", "accDescr"))]
    if not body:
        return ["圖表沒有內容"]
    if kind in ("flowchart", "graph"):
        return _lint_flowchart(body)
    if kind == "sequenceDiagram":
        return _lint_sequence(body)
    if kind == "erDiagram":
        return _lint_er(body)
    if kind == "usecase-beta":
        return _lint_usecase(body)
    if kind.startswith(("classDiagram", "stateDiagram")):
        return _lint_braces(body)
    return []


# --- Mermaid 自動修正 ---

_SHAPES = [  # 長的放前面，避免 [[ 被當成 [
    ("(((", ")))"),
    ("[[", "]]"),
    ("[(", ")]"),
    ("((", "))"),
    ("([", "])"),
    ("{{", "}}"),
    ("[", "]"),
    ("(", ")"),
    ("{", "}"),
]
_ID_CHARS = r"A-Za-z0-9_À-￿"
_NODE_RE = re.compile(rf"([{_ID_CHARS}]+)(\(\(\(|\[\[|\[\(|\(\(|\(\[|\{{\{{|\[/|\[\\|\[|\(|\{{)")
# 實測 Mermaid 12：節點文字有這些符號會解析失敗，要用雙引號包起來（; # & < > : 沒問題）
_LABEL_SPECIAL = set('()[]{}|"@')
_DELIMS = set(" \t-&;:=.~|>")


def _needs_quote(label: str) -> bool:
    s = label.strip()
    if not s or (s.startswith('"') and s.endswith('"') and len(s) >= 2):
        return False
    return any(ch in _LABEL_SPECIAL for ch in s) or s[0] in "/\\"


def _quote(label: str) -> str:
    return '"' + label.strip().replace('"', "#quot;") + '"'


def _find_close(line: str, start: int, close: str) -> int:
    """找節點形狀的結尾。雙引號裡的括號不算（例如 A["輸入 [必填]"]）。"""
    i = start
    while i < len(line):
        if line[i] == '"':
            k = line.find('"', i + 1)
            i = i + 1 if k == -1 else k + 1  # 沒有成對的引號就當一般字元
            continue
        if line.startswith(close, i):
            after = i + len(close)
            if after >= len(line) or line[after] in _DELIMS or line.startswith(":::", after):
                return i
        i += 1
    return -1


def _quote_flowchart_line(line: str) -> str:
    out: list[str] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch == '"':  # 已經用引號包起來的文字原樣保留
            k = line.find('"', i + 1)
            k = n - 1 if k == -1 else k
            out.append(line[i : k + 1])
            i = k + 1
            continue
        if ch == "|":  # 連線文字 -->|文字|
            k = line.find("|", i + 1)
            if k == -1:
                out.append(line[i:])
                break
            label = line[i + 1 : k]
            out.append("|" + (_quote(label) if _needs_quote(label) else label) + "|")
            i = k + 1
            continue
        m = _NODE_RE.match(line, i)
        if m and (i == 0 or not re.match(rf"[{_ID_CHARS}]", line[i - 1])):
            node_id, opener = m.group(1), m.group(2)
            if opener in ("[/", "[\\"):
                k = _find_close(line, m.end(), "]")
                if k != -1 and line[k - 1] in "/\\":  # 真的是平行四邊形/梯形，不處理
                    out.append(line[i : k + 1])
                    i = k + 1
                    continue
                opener = "["  # 只是文字剛好以 / 開頭，例如 A[/api/login]
                m = re.match(rf"([{_ID_CHARS}]+)\[", line[i:])
                assert m is not None
                start = i + m.end()
            else:
                start = m.end()
            close = dict(_SHAPES)[opener]
            end = _find_close(line, start, close)
            if end == -1:
                out.append(line[i:])
                break
            label = line[start:end]
            out.append(node_id + opener + (_quote(label) if _needs_quote(label) else label) + close)
            i = end + len(close)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_EDGE_TEXT_FORMS = [
    (re.compile(r"(?<![-.=<])--\s+(?![->])([^\"|\n]+?)\s+-->"), "-->|{}|"),
    (re.compile(r"(?<![-.=<])-\.\s+([^\"|\n]+?)\s+\.->"), "-.->|{}|"),
    (re.compile(r"(?<![-.=<])==\s+(?![=>])([^\"|\n]+?)\s+==>"), "==>|{}|"),
]


def _fix_flowchart_line(line: str) -> str:
    stripped = line.strip()
    if (
        not stripped
        or stripped.startswith(("%%",) + _FLOW_SKIP_PREFIXES)
        or stripped in ("end",)
        or "@{" in stripped
    ):
        return line
    for pattern, repl in _EDGE_TEXT_FORMS:
        line = pattern.sub(lambda m, r=repl: r.format(m.group(1).strip()), line)
    if stripped.startswith("subgraph"):
        indent = line[: len(line) - len(line.lstrip())]
        rest = line.strip()[len("subgraph") :]
        return indent + "subgraph" + _quote_flowchart_line(rest)
    return _quote_flowchart_line(line)


def fix_mermaid(code: str) -> str:
    code = code.replace("\r\n", "\n").strip()
    lines = code.splitlines()
    # 去掉圖表類型前面的說明文字
    for i, ln in enumerate(lines):
        word = ln.strip().split(" ")[0].rstrip(";") if ln.strip() else ""
        if word in MERMAID_KEYWORDS or ln.strip() == "---" or ln.strip().startswith("%%"):
            lines = lines[i:]
            break
    code = "\n".join(lines)
    if mermaid_kind(code) in ("flowchart", "graph"):
        fixed: list[str] = []
        seen_kind = False
        for ln in code.splitlines():
            if not seen_kind and mermaid_kind(ln) in ("flowchart", "graph"):
                seen_kind = True
                fixed.append(ln)
                continue
            fixed.append(_fix_flowchart_line(ln) if seen_kind else ln)
        code = "\n".join(fixed)
    elif mermaid_kind(code) == "sequenceDiagram":
        # 訊息裡的半形分號會被當成換行，改成全形
        fixed = []
        for ln in code.splitlines():
            if _SEQ_ARROW_RE.search(ln) and ":" in ln:
                head, msg = ln.split(":", 1)
                ln = f"{head}:{msg.replace(';', '；')}"
            fixed.append(ln)
        code = "\n".join(fixed)
    return code


# ---------------------------------------------------------------------------
# PlantUML
# ---------------------------------------------------------------------------

_PUML_START_RE = re.compile(r"^\s*@start(\w+)", re.M)


def fix_plantuml(code: str) -> str:
    code = code.replace("\r\n", "\n").strip()
    m = _PUML_START_RE.search(code)
    if not m:
        return f"@startuml\n{code}\n@enduml"
    kind = m.group(1)
    code = code[m.start() :].lstrip()
    end_tag = f"@end{kind}"
    k = code.find(end_tag)
    if k == -1:
        code = code.rstrip() + f"\n{end_tag}"
    else:
        code = code[: k + len(end_tag)]
    return code


def lint_plantuml(code: str) -> list[str]:
    errors: list[str] = []
    m = _PUML_START_RE.search(code)
    if not m:
        return ["缺少 @startuml"]
    kind = m.group(1)
    if f"@end{kind}" not in code:
        errors.append(f"缺少 @end{kind}")
    if "```" in code:
        errors.append("圖表程式碼裡不能有 ``` 符號")
    inner = re.sub(r"@start\w+.*|@end\w+", "", code).strip()
    if not inner:
        errors.append("圖表沒有內容")
    elif inner.split()[0].rstrip(";") in MERMAID_KEYWORDS - {"block", "packet"}:
        errors.append("這是 Mermaid 語法，這裡要用 PlantUML 語法")
    return errors


# ---------------------------------------------------------------------------


def normalize_diagram(lang: str, code: str) -> tuple[str, list[str]]:
    """修正常見錯誤後再檢查，回傳 (修正後程式碼, 剩下的錯誤)。"""
    if lang == "plantuml":
        code = fix_plantuml(code)
        return code, lint_plantuml(code)
    code = fix_mermaid(code)
    return code, lint_mermaid(code)


def lint_markdown_diagrams(markdown: str) -> list[tuple[int, DiagramBlock, list[str]]]:
    """回傳每個有問題的圖表：(第幾個圖, 區塊, 錯誤列表)。"""
    problems = []
    for idx, block in enumerate(extract_diagrams(markdown)):
        errs = lint_plantuml(block.code) if block.lang == "plantuml" else lint_mermaid(block.code)
        if errs:
            problems.append((idx, block, errs))
    return problems
