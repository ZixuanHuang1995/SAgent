"""文件範本：每種文件類型的建議章節和建議的圖。

內建範本在 sagent/doc_templates/*.yaml。
使用者可以在 data/templates/ 放自己的 yaml，key 相同時會蓋過內建範本。

範本的選用欄位：
- category：類型（見 CATEGORIES），新增 Spec 時先選類型再選文件類型，預設 feature
- role：撰寫這種文件的角色，會放進提示詞，預設「系統分析師」
- style：寫作規則。spec 規格文件（預設）、advice 建議類文件、idea 構想文件、decision 技術決策文件、
  algorithm 演算法設計文件、experiment 實驗設計文件
- advice：舊寫法，true 等於 style: advice
- allow_diagrams：false 表示這種文件不畫圖
- max_diagrams：這種文件最多畫幾張圖，會蓋過畫面上設定的數量（只會變少）
- fixed_sections：true 表示章節固定，AI 撰寫時不刪、不加、不改名

章節的選用欄位：
- diagrams：建議的圖（見 diagram_kinds.KINDS）
- ids：這一節的項目用什麼編號，例如 FR。沒寫時從目標裡的「編號 FR-01」找（舊寫法）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..services.markdown_doc import normalize_heading
from .diagram_kinds import KINDS

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "doc_templates"


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    description: str


# 類型：這件事想得多清楚了。順序就是畫面上的順序
CATEGORIES: list[Category] = [
    Category(
        "idea", "Concept", "只有想法，還沒確定要不要做。整理問題、使用者故事、怎樣算成功和可能的做法。"
    ),
    Category("feature", "Functionality", "已經確定要做這個功能。寫清楚功能、規則和驗收條件，再安排測試。"),
    Category(
        "design",
        "Technical",
        "要決定怎麼實作。先比較幾個做法、選出一個，再寫系統架構和資料庫設計。",
    ),
    Category(
        "algorithm",
        "Algorithm",
        "要設計演算法。定義輸入、輸出和限制，說明設計策略、解法、虛擬碼、複雜度和測試案例。",
    ),
    Category("change", "Change", "要修改既有系統。說明要改什麼、為什麼改、影響範圍和怎麼上線。"),
]
CATEGORY_KEYS = {c.key for c in CATEGORIES}
STYLES = ("spec", "advice", "idea", "decision", "algorithm", "experiment")


@dataclass
class TemplateSection:
    heading: str
    goal: str = ""
    diagrams: list[str] = field(default_factory=list)
    # 這一節定義的項目用什麼編號，例如 FR 表示 FR-01、FR-02…；空字串表示這一節不新增編號
    ids: str = ""


_IDS_RE = re.compile(r"^[A-Z]{1,4}$")
# 舊寫法：編號寫在目標裡，例如「編號 FR-01、FR-02…」
_GOAL_IDS_RE = re.compile(r"編號\s*[：:]?\s*([A-Z]{1,4})-0?1\b")


def ids_from_goal(goal: str) -> str:
    m = _GOAL_IDS_RE.search(goal)
    return m.group(1) if m else ""


def same_heading(a: str, b: str) -> bool:
    """章節名稱一樣，或其中一個包含另一個（「功能需求」和「功能需求說明」）。"""
    a, b = normalize_heading(a), normalize_heading(b)
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 2 and (a in b or b in a))


@dataclass
class DocTemplate:
    key: str
    name: str
    description: str = ""
    sections: list[TemplateSection] = field(default_factory=list)
    order: int = 100
    builtin: bool = True
    role: str = "系統分析師"
    style: str = "spec"
    allow_diagrams: bool = True
    category: str = "feature"
    max_diagrams: int | None = None
    # 章節固定：AI 撰寫時章節名稱和順序照範本，Planner 只決定每節的要點和圖
    fixed_sections: bool = False

    def section_for(self, heading: str) -> TemplateSection | None:
        """找出跟這個標題對應的範本章節。先找完全一樣的，再找互相包含的。"""
        key = normalize_heading(heading)
        exact = next((s for s in self.sections if normalize_heading(s.heading) == key), None)
        return exact or next((s for s in self.sections if same_heading(s.heading, heading)), None)

    @property
    def advice(self) -> bool:
        return self.style == "advice"

    @property
    def idea(self) -> bool:
        return self.style == "idea"

    @property
    def decision(self) -> bool:
        return self.style == "decision"

    @property
    def algorithm(self) -> bool:
        return self.style == "algorithm"

    @property
    def experiment(self) -> bool:
        return self.style == "experiment"

    def skeleton(self, title: str) -> str:
        """手動建立 Spec 時用的空白骨架。"""
        parts = [f"# {title}", f"> 文件類型：{self.name}"]
        for i, sec in enumerate(self.sections, start=1):
            block = f"## {i}. {sec.heading}"
            hint = sec.goal
            if sec.ids:
                hint += f"編號 {sec.ids}-01、{sec.ids}-02…。"
            if hint:
                block += f"\n\n_（撰寫提示：{hint}）_"
            parts.append(block)
        return "\n\n".join(parts) + "\n"

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "builtin": self.builtin,
            "category": self.category,
            "style": self.style,
            "advice": self.advice,
            "allow_diagrams": self.allow_diagrams,
            "fixed_sections": self.fixed_sections,
            "sections": [
                {"heading": s.heading, "goal": s.goal, "diagrams": s.diagrams, "ids": s.ids}
                for s in self.sections
            ],
        }


def _parse(path: Path, builtin: bool) -> DocTemplate:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sections = []
    for raw in data.get("sections") or []:
        if not isinstance(raw, dict) or not str(raw.get("heading", "")).strip():
            continue
        goal = str(raw.get("goal", "")).strip()
        ids = str(raw.get("ids") or "").strip().upper()
        sections.append(
            TemplateSection(
                heading=str(raw["heading"]).strip(),
                goal=goal,
                diagrams=[d for d in (raw.get("diagrams") or []) if d in KINDS],
                ids=ids if _IDS_RE.match(ids) else ids_from_goal(goal),
            )
        )
    return DocTemplate(
        key=str(data.get("key") or path.stem),
        name=str(data.get("name") or path.stem),
        description=str(data.get("description") or ""),
        sections=sections,
        order=int(data.get("order", 100)),
        builtin=builtin,
        role=str(data.get("role") or "").strip() or "系統分析師",
        style=_style(data),
        allow_diagrams=data.get("allow_diagrams") is not False,
        category=data["category"] if data.get("category") in CATEGORY_KEYS else "feature",
        max_diagrams=_max_diagrams(data.get("max_diagrams")),
        fixed_sections=data.get("fixed_sections") is True,
    )


def _max_diagrams(raw: object) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return raw


def _style(data: dict) -> str:
    style = str(data.get("style") or "").strip()
    if style in STYLES:
        return style
    return "advice" if data.get("advice") is True else "spec"


def load_templates(user_dir: Path | None = None) -> dict[str, DocTemplate]:
    templates: dict[str, DocTemplate] = {}
    sources = [(BUILTIN_DIR, True)]
    if user_dir is not None:
        sources.append((user_dir, False))
    for folder, builtin in sources:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.y*ml")):
            try:
                tpl = _parse(path, builtin)
            except (yaml.YAMLError, OSError, ValueError, TypeError):
                continue  # 寫壞的範本跳過，不影響其他範本
            if tpl.sections:
                templates[tpl.key] = tpl
    return dict(sorted(templates.items(), key=lambda kv: (kv[1].order, kv[0])))


def get_template(templates: dict[str, DocTemplate], key: str) -> DocTemplate:
    if key in templates:
        return templates[key]
    return next(iter(templates.values()))
