"""資料表定義。

結構：專案 → 功能階段 → Spec → 版本（不可修改）。
基準版本（Baseline）記錄某個時間點每份 Spec 用的是哪一版。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, utcnow

SPEC_STATUSES = ("draft", "reviewing", "approved", "deprecated")
VERSION_SOURCES = ("manual", "agent", "restore", "import", "template")
RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
RUN_MODES = ("draft", "revise")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    code: Mapped[str] = mapped_column(String(40), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    phases: Mapped[list[Phase]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Phase.sort_order"
    )
    specs: Mapped[list[Spec]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Phase(Base):
    """功能階段，例如「需求分析」「系統設計」「第二期」。名稱由使用者自訂。"""

    __tablename__ = "phases"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    project: Mapped[Project] = relationship(back_populates="phases")


class Spec(Base):
    __tablename__ = "specs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    phase_id: Mapped[int] = mapped_column(ForeignKey("phases.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    doc_type: Mapped[str] = mapped_column(String(80), default="functional_spec")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # 最新版本號。0 表示還沒有任何版本（例如剛建立、等 AI 寫第一版）
    head_version_no: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="specs")
    phase: Mapped[Phase] = relationship()
    versions: Mapped[list[SpecVersion]] = relationship(
        back_populates="spec", cascade="all, delete-orphan", order_by="SpecVersion.version_no"
    )


class SpecVersion(Base):
    """Spec 的一個版本。建立後內容不再修改，只有 label 可以改。"""

    __tablename__ = "spec_versions"
    __table_args__ = (UniqueConstraint("spec_id", "version_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("specs.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    # 版本標籤，例如「v1.0」「客戶確認版」，同一份 Spec 內不可重複
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 跟前一版比較新增、刪除幾行（第 1 版：全部算新增）
    lines_added: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lines_removed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    spec: Mapped[Spec] = relationship(back_populates="versions")


class Baseline(Base):
    """專案的基準版本：把當下每份 Spec 的最新版記錄下來，例如「需求凍結 v1.0」。"""

    __tablename__ = "baselines"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(Text, default="")
    # 只收某個階段的 Spec 時記錄階段；None 表示整個專案
    phase_id: Mapped[int | None] = mapped_column(ForeignKey("phases.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    items: Mapped[list[BaselineItem]] = relationship(back_populates="baseline", cascade="all, delete-orphan")


class BaselineItem(Base):
    __tablename__ = "baseline_items"
    __table_args__ = (UniqueConstraint("baseline_id", "spec_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    baseline_id: Mapped[int] = mapped_column(ForeignKey("baselines.id", ondelete="CASCADE"), index=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("specs.id", ondelete="CASCADE"))
    version_id: Mapped[int] = mapped_column(ForeignKey("spec_versions.id", ondelete="CASCADE"))
    version_no: Mapped[int] = mapped_column(Integer)

    baseline: Mapped[Baseline] = relationship(back_populates="items")
    spec: Mapped[Spec] = relationship()


class AgentRun(Base):
    """一次 AI 撰寫工作（planner → writer → reviewer）。"""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("specs.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="draft")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    model: Mapped[str] = mapped_column(String(200), default="")
    doc_type: Mapped[str] = mapped_column(String(80), default="")
    requirement: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 執行開始時的 Spec 版本號，用來判斷執行期間有沒有人改過文件
    base_version_no: Mapped[int] = mapped_column(Integer, default=0)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    review: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    spec: Mapped[Spec] = relationship()


class AgentEvent(Base):
    """執行過程的紀錄（步驟開始/結束、錯誤等）。逐字串流不存，只在畫面上即時顯示。"""

    __tablename__ = "agent_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(40))
    agent: Mapped[str] = mapped_column(String(20), default="system")
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
