"""文件範本：每種文件類型的建議章節和建議的圖。

內建範本在 sagent/doc_templates/*.yaml。
使用者可以在 data/templates/ 放自己的 yaml，key 相同時會蓋過內建範本。

範本的選用欄位：
- role：撰寫這種文件的角色，會放進提示詞，預設「系統分析師」
- advice：true 表示建議類文件（例如 UI/UX 設計建議），可以提出需求沒寫的做法，寫作和審查規則不同
- allow_diagrams：false 表示這種文件不畫圖
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .diagram_kinds import KINDS

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "doc_templates"


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
    advice: bool = False
    allow_diagrams: bool = True

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
        advice=data.get("advice") is True,
        allow_diagrams=data.get("allow_diagrams") is not False,
    )


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
