"""專案、階段、Spec 與版本控制。

每次存檔都會產生新版本，舊版本不會被改掉。內容沒變就不產生新版本。
"""

from __future__ import annotations

import difflib
import hashlib
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, defer

from ..db import utcnow
from ..models import SPEC_STATUSES, VERSION_SOURCES, AgentRun, Phase, Project, Spec, SpecVersion
from .errors import BadRequest, Conflict, NotFound
from .markdown_doc import normalize_heading, parse_markdown

DEFAULT_PHASES = ["需求分析", "系統設計", "詳細設計"]
ACTIVE_RUN_STATUSES = ("queued", "running")


def normalize_content(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip() + "\n" if text.strip() else ""


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 專案
# ---------------------------------------------------------------------------


def get_project(s: Session, project_id: int) -> Project:
    project = s.get(Project, project_id)
    if project is None:
        raise NotFound("找不到這個專案")
    return project


def create_project(
    s: Session, name: str, code: str = "", description: str = "", phases: list[str] | None = None
) -> Project:
    name = name.strip()
    if not name:
        raise BadRequest("專案名稱不能空白")
    if s.scalar(select(Project).where(Project.name == name)):
        raise Conflict(f"已經有叫「{name}」的專案")
    project = Project(name=name, code=code.strip(), description=description.strip())
    s.add(project)
    s.flush()
    names = DEFAULT_PHASES if phases is None else phases
    for i, phase_name in enumerate(dict.fromkeys(p.strip() for p in names if p.strip())):
        s.add(Phase(project_id=project.id, name=phase_name, sort_order=i))
    s.flush()
    return project


def update_project(s: Session, project: Project, **fields: Any) -> Project:
    if "name" in fields and fields["name"] is not None:
        name = fields["name"].strip()
        if not name:
            raise BadRequest("專案名稱不能空白")
        dup = s.scalar(select(Project).where(Project.name == name, Project.id != project.id))
        if dup:
            raise Conflict(f"已經有叫「{name}」的專案")
        project.name = name
    for key in ("code", "description"):
        if fields.get(key) is not None:
            setattr(project, key, fields[key].strip())
    project.updated_at = utcnow()
    s.flush()
    return project


def delete_project(s: Session, project: Project) -> None:
    active = s.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.project_id == project.id, AgentRun.status.in_(ACTIVE_RUN_STATUSES)
        )
    )
    if active:
        raise Conflict("專案還有 AI 工作在執行，請先取消")
    s.delete(project)
    s.flush()


def project_counts(s: Session, project_id: int) -> tuple[int, int]:
    phases = s.scalar(select(func.count(Phase.id)).where(Phase.project_id == project_id)) or 0
    specs = (
        s.scalar(select(func.count(Spec.id)).where(Spec.project_id == project_id, Spec.archived.is_(False)))
        or 0
    )
    return phases, specs


# ---------------------------------------------------------------------------
# 階段
# ---------------------------------------------------------------------------


def get_phase(s: Session, phase_id: int) -> Phase:
    phase = s.get(Phase, phase_id)
    if phase is None:
        raise NotFound("找不到這個階段")
    return phase


def create_phase(s: Session, project: Project, name: str, description: str = "") -> Phase:
    name = name.strip()
    if not name:
        raise BadRequest("階段名稱不能空白")
    if s.scalar(select(Phase).where(Phase.project_id == project.id, Phase.name == name)):
        raise Conflict(f"這個專案已經有「{name}」階段")
    max_order = s.scalar(select(func.max(Phase.sort_order)).where(Phase.project_id == project.id))
    phase = Phase(
        project_id=project.id,
        name=name,
        description=description.strip(),
        sort_order=(max_order + 1) if max_order is not None else 0,
    )
    s.add(phase)
    s.flush()
    return phase


def update_phase(s: Session, phase: Phase, **fields: Any) -> Phase:
    if fields.get("name") is not None:
        name = fields["name"].strip()
        if not name:
            raise BadRequest("階段名稱不能空白")
        dup = s.scalar(
            select(Phase).where(
                Phase.project_id == phase.project_id, Phase.name == name, Phase.id != phase.id
            )
        )
        if dup:
            raise Conflict(f"這個專案已經有「{name}」階段")
        phase.name = name
    if fields.get("description") is not None:
        phase.description = fields["description"].strip()
    if fields.get("sort_order") is not None:
        phase.sort_order = int(fields["sort_order"])
    s.flush()
    return phase


def reorder_phases(s: Session, project: Project, phase_ids: list[int]) -> None:
    phases = {p.id: p for p in project.phases}
    if set(phase_ids) != set(phases):
        raise BadRequest("階段清單不完整")
    for i, pid in enumerate(phase_ids):
        phases[pid].sort_order = i
    s.flush()


def delete_phase(s: Session, phase: Phase) -> None:
    count = s.scalar(select(func.count(Spec.id)).where(Spec.phase_id == phase.id)) or 0
    if count:
        raise Conflict("階段裡還有 Spec（含已封存），請先移到其他階段")
    s.delete(phase)
    s.flush()


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------


def get_spec(s: Session, spec_id: int) -> Spec:
    spec = s.get(Spec, spec_id)
    if spec is None:
        raise NotFound("找不到這份 Spec")
    return spec


def list_specs(
    s: Session,
    project_id: int,
    *,
    phase_id: int | None = None,
    archived: bool = False,
    q: str = "",
    status: str | None = None,
) -> list[Spec]:
    stmt = select(Spec).where(Spec.project_id == project_id, Spec.archived.is_(archived))
    if phase_id is not None:
        stmt = stmt.where(Spec.phase_id == phase_id)
    if status:
        stmt = stmt.where(Spec.status == status)
    if q.strip():
        head = aliased(SpecVersion)
        stmt = stmt.outerjoin(
            head, and_(head.spec_id == Spec.id, head.version_no == Spec.head_version_no)
        ).where(
            or_(
                Spec.title.contains(q.strip(), autoescape=True),
                head.content.contains(q.strip(), autoescape=True),
            )
        )
    return list(s.scalars(stmt.order_by(Spec.phase_id, Spec.title)))


def create_spec(
    s: Session,
    project: Project,
    *,
    phase_id: int,
    title: str,
    doc_type: str = "functional_spec",
    content: str | None = None,
    source: str = "manual",
    message: str = "",
) -> Spec:
    title = title.strip()
    if not title:
        raise BadRequest("Spec 標題不能空白")
    phase = get_phase(s, phase_id)
    if phase.project_id != project.id:
        raise BadRequest("階段不屬於這個專案")
    spec = Spec(project_id=project.id, phase_id=phase.id, title=title, doc_type=doc_type)
    s.add(spec)
    s.flush()
    if content is not None and content.strip():
        add_version(s, spec, content, message=message or "建立文件", source=source)
    project.updated_at = utcnow()
    return spec


def update_spec(s: Session, spec: Spec, **fields: Any) -> Spec:
    if fields.get("title") is not None:
        title = fields["title"].strip()
        if not title:
            raise BadRequest("Spec 標題不能空白")
        spec.title = title
    if fields.get("phase_id") is not None:
        phase = get_phase(s, fields["phase_id"])
        if phase.project_id != spec.project_id:
            raise BadRequest("階段不屬於這個專案")
        spec.phase_id = phase.id
    if fields.get("status") is not None:
        if fields["status"] not in SPEC_STATUSES:
            raise BadRequest("狀態不正確")
        spec.status = fields["status"]
    if fields.get("doc_type") is not None:
        spec.doc_type = fields["doc_type"]
    spec.updated_at = utcnow()
    s.flush()
    return spec


def set_archived(s: Session, spec: Spec, archived: bool) -> Spec:
    if archived and active_run_for_spec(s, spec.id):
        raise Conflict("這份 Spec 有 AI 工作在執行，請先取消")
    spec.archived = archived
    spec.updated_at = utcnow()
    s.flush()
    return spec


def active_run_for_spec(s: Session, spec_id: int) -> AgentRun | None:
    return s.scalar(
        select(AgentRun)
        .where(AgentRun.spec_id == spec_id, AgentRun.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(AgentRun.id.desc())
    )


# ---------------------------------------------------------------------------
# 版本
# ---------------------------------------------------------------------------


def get_head(s: Session, spec: Spec) -> SpecVersion | None:
    if spec.head_version_no <= 0:
        return None
    return get_version(s, spec, spec.head_version_no)


def get_version(s: Session, spec: Spec, version_no: int) -> SpecVersion:
    version = s.scalar(
        select(SpecVersion).where(SpecVersion.spec_id == spec.id, SpecVersion.version_no == version_no)
    )
    if version is None:
        raise NotFound(f"找不到第 {version_no} 版")
    return version


def list_versions(s: Session, spec: Spec) -> list[SpecVersion]:
    stmt = (
        select(SpecVersion)
        .options(defer(SpecVersion.content))
        .where(SpecVersion.spec_id == spec.id)
        .order_by(SpecVersion.version_no.desc())
    )
    return list(s.scalars(stmt))


def add_version(
    s: Session,
    spec: Spec,
    content: str,
    *,
    message: str = "",
    source: str = "manual",
    run_id: int | None = None,
    base_version_no: int | None = None,
) -> tuple[SpecVersion, bool]:
    """存成新版本。回傳 (版本, 是否真的新增)。內容跟最新版一樣就不新增。

    base_version_no：編輯時看到的版本號。跟目前最新版不同代表別人先存了，回 409。
    """
    if source not in VERSION_SOURCES:
        raise BadRequest("版本來源不正確")
    if base_version_no is not None and base_version_no != spec.head_version_no:
        raise Conflict(
            f"文件已經被更新到第 {spec.head_version_no} 版（你編輯的是第 {base_version_no} 版），請先重新載入再存檔"
        )
    content = normalize_content(content)
    digest = content_hash(content)
    head = get_head(s, spec)
    if head is not None and head.content_hash == digest:
        return head, False
    added, removed = line_stats(head.content if head is not None else "", content)
    version = SpecVersion(
        spec_id=spec.id,
        version_no=spec.head_version_no + 1,
        content=content,
        content_hash=digest,
        message=message.strip(),
        source=source,
        run_id=run_id,
        lines_added=added,
        lines_removed=removed,
    )
    s.add(version)
    spec.head_version_no = version.version_no
    spec.updated_at = utcnow()
    try:
        s.flush()
    except IntegrityError as exc:  # 兩個人同時存檔
        raise Conflict("文件剛被其他人更新，請重新載入再存檔") from exc
    return version, True


def restore_version(s: Session, spec: Spec, version_no: int, message: str = "") -> tuple[SpecVersion, bool]:
    old = get_version(s, spec, version_no)
    return add_version(s, spec, old.content, message=message or f"還原到第 {version_no} 版", source="restore")


def set_label(s: Session, spec: Spec, version_no: int, label: str | None) -> SpecVersion:
    version = get_version(s, spec, version_no)
    label = (label or "").strip() or None
    if label:
        dup = s.scalar(
            select(SpecVersion).where(
                SpecVersion.spec_id == spec.id, SpecVersion.label == label, SpecVersion.id != version.id
            )
        )
        if dup:
            raise Conflict(f"標籤「{label}」已經用在第 {dup.version_no} 版")
    version.label = label
    s.flush()
    return version


# ---------------------------------------------------------------------------
# 差異比較
# ---------------------------------------------------------------------------


def line_stats(old: str, new: str) -> tuple[int, int]:
    """新增、刪除幾行。算法跟 diff_texts 一樣，數字才會跟比較畫面一致。"""
    a = old.splitlines()
    b = new.splitlines()
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def diff_texts(old: str, new: str) -> dict[str, Any]:
    """逐行比較。回傳每一行的狀態，以及章節層級的變更摘要。"""
    a = old.splitlines()
    b = new.splitlines()
    lines: list[dict[str, Any]] = []
    added = removed = 0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                lines.append({"t": "eq", "a": i1 + k + 1, "b": j1 + k + 1, "text": a[i1 + k]})
            continue
        if tag in ("replace", "delete"):
            for k in range(i1, i2):
                lines.append({"t": "del", "a": k + 1, "b": None, "text": a[k]})
                removed += 1
        if tag in ("replace", "insert"):
            for k in range(j1, j2):
                lines.append({"t": "add", "a": None, "b": k + 1, "text": b[k]})
                added += 1
    return {
        "stats": {"added": added, "removed": removed},
        "lines": lines,
        "sections": diff_sections(old, new),
    }


def diff_sections(old: str, new: str) -> list[dict[str, str]]:
    """以 ## 章節為單位比較：新增、刪除、修改、未變更。"""
    old_doc, new_doc = parse_markdown(old), parse_markdown(new)
    old_map = {normalize_heading(sec.heading): sec for sec in old_doc.sections}
    new_keys = set()
    result: list[dict[str, str]] = []
    for sec in new_doc.sections:
        key = normalize_heading(sec.heading)
        new_keys.add(key)
        prev = old_map.get(key)
        if prev is None:
            status = "added"
        elif prev.body.strip() == sec.body.strip():
            status = "unchanged"
        else:
            status = "modified"
        result.append({"heading": sec.heading, "status": status})
    for key, sec in old_map.items():
        if key not in new_keys:
            result.append({"heading": sec.heading, "status": "removed"})
    return result
