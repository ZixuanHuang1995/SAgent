"""文件範本：每種文件類型的建議章節和建議的圖。

內建範本在 sagent/doc_templates/*.yaml。
使用者可以在 data/templates/ 放自己的 yaml，key 相同時會蓋過內建範本。

範本的選用欄位：
- category：類型（見 CATEGORIES），新增 Spec 時先選類型再選文件類型，預設 feature
- role：撰寫這種文件的角色，會放進提示詞，預設「系統分析師」
- style：寫作規則。spec 規格文件（預設）、advice 建議類文件、idea 構想文件
- advice：舊寫法，true 等於 style: advice
- allow_diagrams：false 表示這種文件不畫圖
- max_diagrams：這種文件最多畫幾張圖，會蓋過畫面上設定的數量（只會變少）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .diagram_kinds import KINDS

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "doc_templates"


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    description: str


# 類型：這件事想得多清楚了。順序就是畫面上的順序
CATEGORIES: list[Category] = [
    Category("idea", "構想", "只有想法，還沒確定要不要做。整理問題、目標和可能的做法。"),
    Category("feature", "功能", "已經確定要做這個功能。寫清楚要做什麼、規則和驗收條件。"),
    Category("design", "技術設計", "已經有功能規格，要設計怎麼實作：架構、資料庫、API。"),
    Category("change", "變更", "要修改既有系統的功能。說明改什麼、影響範圍和上線方式。"),
]
CATEGORY_KEYS = {c.key for c in CATEGORIES}
STYLES = ("spec", "advice", "idea")


@dataclass
class TemplateSection:
    heading: str
    goal: str = ""
    diagrams: list[str] = field(default_factory=list)


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

    @property
    def advice(self) -> bool:
        return self.style == "advice"

    @property
    def idea(self) -> bool:
        return self.style == "idea"

    def skeleton(self, title: str) -> str:
        """手動建立 Spec 時用的空白骨架。"""
        parts = [f"# {title}", f"> 文件類型：{self.name}"]
        for i, sec in enumerate(self.sections, start=1):
            block = f"## {i}. {sec.heading}"
            if sec.goal:
                block += f"\n\n_（撰寫提示：{sec.goal}）_"
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
            "sections": [
                {"heading": s.heading, "goal": s.goal, "diagrams": s.diagrams} for s in self.sections
            ],
        }


def _parse(path: Path, builtin: bool) -> DocTemplate:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sections = []
    for raw in data.get("sections") or []:
        if not isinstance(raw, dict) or not str(raw.get("heading", "")).strip():
            continue
        sections.append(
            TemplateSection(
                heading=str(raw["heading"]).strip(),
                goal=str(raw.get("goal", "")).strip(),
                diagrams=[d for d in (raw.get("diagrams") or []) if d in KINDS],
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
