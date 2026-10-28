"""三個 agent 之間傳遞的資料。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .doc_templates import DocTemplate

OPEN_QUESTIONS_HEADING = "待確認事項"

SectionAction = Literal["write", "keep", "update"]


@dataclass
class PipelineOptions:
    uml_syntax: str = "auto"  # auto | mermaid | plantuml
    plantuml_available: bool = False
    max_revisions: int = 1
    temperature: float = 0.4
    diagrams: bool = True
    max_diagrams: int = 4
    max_sections: int = 10


@dataclass
class RunInput:
    mode: Literal["draft", "revise"]
    project_name: str
    project_description: str
    phase_name: str
    spec_title: str
    template: DocTemplate
    requirement: str
    existing_content: str = ""
    options: PipelineOptions = field(default_factory=PipelineOptions)


@dataclass
class DiagramPlan:
    kind: str
    title: str
    description: str = ""
    syntax: str = "mermaid"  # mermaid | plantuml


@dataclass
class SectionPlan:
    id: str  # S1, S2…
    heading: str
    goal: str = ""
    key_points: list[str] = field(default_factory=list)
    diagrams: list[DiagramPlan] = field(default_factory=list)
    # write：新寫；keep：沿用原內容；update：依修訂說明修改原內容
    action: SectionAction = "write"
    source_body: str = ""
    instruction: str = ""
    # 這一節定義的項目用什麼編號（例如 FR），來自範本章節的 ids
    ids: str = ""


@dataclass
class Plan:
    title: str
    summary: str
    sections: list[SectionPlan]
    open_questions: list[str] = field(default_factory=list)
    # 模型沒有給出可用的大綱，改用範本章節
    fallback: bool = False
    # 修訂模式：原文件開頭（標題、文件資訊）和原本「待確認事項」整段內容
    preamble: str = ""
    existing_questions_body: str = ""

    def section(self, section_id: str) -> SectionPlan | None:
        return next((s for s in self.sections if s.id == section_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "fallback": self.fallback,
            "open_questions": self.open_questions,
            "sections": [
                {
                    "id": s.id,
                    "heading": s.heading,
                    "goal": s.goal,
                    "key_points": s.key_points,
                    "action": s.action,
                    "instruction": s.instruction,
                    "ids": s.ids,
                    "diagrams": [asdict(d) for d in s.diagrams],
                }
                for s in self.sections
            ],
        }


@dataclass
class SectionDraft:
    id: str
    heading: str
    body: str


@dataclass
class Draft:
    sections: list[SectionDraft]

    def section(self, section_id: str) -> SectionDraft | None:
        return next((s for s in self.sections if s.id == section_id), None)


@dataclass
class Issue:
    section_id: str | None
    severity: Literal["high", "medium", "low"]
    problem: str
    suggestion: str = ""
    source: Literal["check", "llm"] = "llm"
    # 圖表語法錯誤時記錄是第幾張圖（章節內從 0 起算）
    diagram_index: int | None = None


@dataclass
class ReviewPoint:
    """需要人工覆核的地方。只列給使用者確認，不會交給 Writer 自動修改。"""

    section_id: str | None
    item: str
    reason: str = ""
    # check：程式找到的（文件標註了假設、待確認）；llm：審查模型找到的
    source: Literal["check", "llm"] = "llm"


@dataclass
class Review:
    """審查結果。issues 是格式、邏輯錯誤，會交給 Writer 修正；review_points 是要人工覆核的地方。

    舊版的審查有 1～10 分，現在不打分數（舊資料裡的 score 保留在資料庫，畫面照樣顯示）。
    """

    round: int
    summary: str
    issues: list[Issue] = field(default_factory=list)
    review_points: list[ReviewPoint] = field(default_factory=list)
    llm_ok: bool = True

    @property
    def needs_revision(self) -> bool:
        return any(i.severity in ("high", "medium") for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "summary": self.summary,
            "verdict": "revise" if self.needs_revision else "pass",
            "llm_ok": self.llm_ok,
            "issues": [asdict(i) for i in self.issues],
            "review_points": [asdict(p) for p in self.review_points],
        }


@dataclass
class AgentStats:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0


@dataclass
class PipelineResult:
    markdown: str
    plan: Plan
    review: Review | None
    reviews: list[Review]
    stats: dict[str, AgentStats]
    summary: str
