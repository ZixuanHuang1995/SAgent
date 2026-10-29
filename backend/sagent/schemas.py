"""API 的輸入與輸出格式。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .agents.planner import GLOSSARY_LIMIT


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- 專案 / 階段 ---


class TermIn(BaseModel):
    """名詞表、專案詞彙表的一項。"""

    name: str = Field(max_length=30)
    kind: str = ""
    note: str = Field("", max_length=100)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field("", max_length=40)
    description: str = ""
    phases: list[str] | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    code: str | None = Field(None, max_length=40)
    description: str | None = None
    glossary: list[TermIn] | None = Field(None, max_length=GLOSSARY_LIMIT)


class ProjectOut(ORMModel):
    id: int
    name: str
    code: str
    description: str
    glossary: list[dict[str, Any]] = []
    created_at: datetime
    updated_at: datetime
    phase_count: int = 0
    spec_count: int = 0
    # 各階段和底下的 Spec 數量（不含封存），依階段順序排列，首頁卡片用
    phases: list[PhaseSummary] = []
    # 執行中、排隊中的 AI 工作數量，首頁卡片用
    running_runs: int = 0
    queued_runs: int = 0


class PhaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class PhaseUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    description: str | None = None
    sort_order: int | None = None


class PhaseOut(ORMModel):
    id: int
    project_id: int
    name: str
    description: str
    sort_order: int


class PhaseSummary(PhaseOut):
    spec_count: int = 0


class PhaseReorder(BaseModel):
    phase_ids: list[int]


# --- Spec / 版本 ---


class SpecCreate(BaseModel):
    phase_id: int
    title: str = Field(min_length=1, max_length=300)
    doc_type: str = "functional_spec"
    # 內容來源：blank 空白、template 依範本骨架、content 使用 content 欄位（例如匯入）
    init: Literal["blank", "template", "content", "none"] = "template"
    content: str | None = None
    # 從哪一份 Spec 轉過來的（例如構想 → 功能規格），要在同一個專案
    source_spec_id: int | None = None


class SpecUpdate(BaseModel):
    title: str | None = Field(None, max_length=300)
    phase_id: int | None = None
    status: Literal["draft", "reviewing", "approved", "deprecated"] | None = None
    doc_type: str | None = None


class ReviewSummary(BaseModel):
    """最近一次成功的 AI 工作的審查結果：項目總數、還沒處理的數量。"""

    run_id: int
    version_no: int | None
    total: int
    open: int


class SpecOut(ORMModel):
    id: int
    project_id: int
    phase_id: int
    title: str
    doc_type: str
    status: str
    head_version_no: int
    archived: bool
    source_spec_id: int | None = None
    created_at: datetime
    updated_at: datetime
    # 只有專案清單和單一 Spec 會填
    review: ReviewSummary | None = None


class VersionMeta(ORMModel):
    id: int
    spec_id: int
    version_no: int
    message: str
    source: str
    run_id: int | None
    label: str | None
    created_at: datetime
    content_hash: str
    lines_added: int | None = None
    lines_removed: int | None = None


class VersionDetail(VersionMeta):
    content: str


class SpecLink(ORMModel):
    id: int
    title: str
    doc_type: str
    archived: bool


class SpecDetail(SpecOut):
    content: str
    head: VersionMeta | None
    active_run_id: int | None
    # 來源 Spec，以及從這份 Spec 轉出去的 Spec
    source_spec: SpecLink | None = None
    derived_specs: list[SpecLink] = []


class ContentSave(BaseModel):
    content: str
    message: str = ""
    base_version_no: int | None = None


class SaveResult(BaseModel):
    created: bool
    version: VersionMeta
    spec: SpecOut


class LabelIn(BaseModel):
    label: str | None = Field(None, max_length=100)


class RestoreIn(BaseModel):
    message: str = ""


class ProjectTree(BaseModel):
    project: ProjectOut
    phases: list[PhaseOut]
    specs: list[SpecOut]


# --- 基準版本 ---


class BaselineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    note: str = ""
    phase_id: int | None = None


class BaselineOut(ORMModel):
    id: int
    project_id: int
    name: str
    note: str
    phase_id: int | None
    created_at: datetime
    item_count: int = 0


# --- AI 工作 ---


class RunOptionsIn(BaseModel):
    model: str | None = None
    uml_syntax: Literal["auto", "mermaid", "plantuml"] | None = None
    max_revisions: int | None = Field(None, ge=0, le=3)
    temperature: float | None = Field(None, ge=0, le=1.5)
    diagrams: bool | None = None
    max_diagrams: int | None = Field(None, ge=0, le=10)
    # 規劃完先停下來，讓使用者回答待確認問題、調整大綱再撰寫（只用在撰寫模式）
    plan_first: bool | None = None


class RunCreate(BaseModel):
    spec_id: int
    mode: Literal["draft", "revise"] = "draft"
    requirement: str = Field(min_length=1)
    doc_type: str | None = None
    options: RunOptionsIn = Field(default_factory=RunOptionsIn)


class AnswerIn(BaseModel):
    question: str
    answer: str = Field(max_length=2000)


class SectionPointsIn(BaseModel):
    id: str
    key_points: list[str] = Field(default_factory=list, max_length=10)


class RunContinue(BaseModel):
    """確認大綱後繼續撰寫：待確認問題的回答、改過的名詞表和各節要點。沒給的部分照 Planner 的。"""

    answers: list[AnswerIn] = Field(default_factory=list, max_length=20)
    terms: list[TermIn] | None = Field(None, max_length=20)
    sections: list[SectionPointsIn] = Field(default_factory=list)


class RunOut(ORMModel):
    id: int
    project_id: int
    spec_id: int
    mode: str
    status: str
    model: str
    doc_type: str
    requirement: str
    options: dict[str, Any]
    base_version_no: int
    result_version_no: int | None
    error: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stats: dict[str, Any] | None
    spec_title: str = ""
    queue_position: int | None = None


class EventOut(BaseModel):
    seq: int
    type: str
    agent: str
    message: str
    data: dict[str, Any]
    ts: str


class RunDetail(RunOut):
    plan: dict[str, Any] | None
    review: dict[str, Any] | None
    review_checked: list[str] = []
    events: list[EventOut]


class ReviewCheckIn(BaseModel):
    key: str = Field(max_length=40)
    done: bool


class RenderIn(BaseModel):
    source: str = Field(min_length=1, max_length=200_000)
