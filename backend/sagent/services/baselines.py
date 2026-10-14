"""基準版本：把專案（或某個階段）當下每份 Spec 的最新版記下來，之後可以比較和匯出。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Baseline, BaselineItem, Phase, Project, Spec, SpecVersion
from .errors import BadRequest, Conflict, NotFound
from .export import ExportEntry, build_zip
from .specs import get_head, get_phase, list_specs


def get_baseline(s: Session, baseline_id: int) -> Baseline:
    baseline = s.get(Baseline, baseline_id)
    if baseline is None:
        raise NotFound("找不到這個基準版本")
    return baseline


def list_baselines(s: Session, project: Project) -> list[Baseline]:
    stmt = select(Baseline).where(Baseline.project_id == project.id).order_by(Baseline.created_at.desc())
    return list(s.scalars(stmt))


def create_baseline(
    s: Session, project: Project, name: str, note: str = "", phase_id: int | None = None
) -> Baseline:
    name = name.strip()
    if not name:
        raise BadRequest("基準版本名稱不能空白")
    if s.scalar(select(Baseline).where(Baseline.project_id == project.id, Baseline.name == name)):
        raise Conflict(f"已經有叫「{name}」的基準版本")
    if phase_id is not None:
        phase = get_phase(s, phase_id)
        if phase.project_id != project.id:
            raise BadRequest("階段不屬於這個專案")
    specs = [sp for sp in list_specs(s, project.id, phase_id=phase_id) if sp.head_version_no > 0]
    if not specs:
        raise BadRequest("沒有可以納入的 Spec（Spec 至少要有一個版本）")
    baseline = Baseline(project_id=project.id, name=name, note=note.strip(), phase_id=phase_id)
    s.add(baseline)
    s.flush()
    for spec in specs:
        head = get_head(s, spec)
        assert head is not None
        s.add(
            BaselineItem(
                baseline_id=baseline.id, spec_id=spec.id, version_id=head.id, version_no=head.version_no
            )
        )
    s.flush()
    return baseline


def delete_baseline(s: Session, baseline: Baseline) -> None:
    s.delete(baseline)
    s.flush()


def baseline_items(s: Session, baseline: Baseline) -> list[dict[str, Any]]:
    rows = s.execute(
        select(BaselineItem, Spec, Phase, SpecVersion)
        .join(Spec, Spec.id == BaselineItem.spec_id)
        .join(Phase, Phase.id == Spec.phase_id)
        .join(SpecVersion, SpecVersion.id == BaselineItem.version_id)
        .where(BaselineItem.baseline_id == baseline.id)
        .order_by(Phase.sort_order, Spec.title)
    ).all()
    return [
        {
            "spec_id": spec.id,
            "title": spec.title,
            "phase_id": phase.id,
            "phase_name": phase.name,
            "version_no": item.version_no,
            "label": version.label,
            "archived": spec.archived,
        }
        for item, spec, phase, version in rows
    ]


def _snapshot(
    s: Session, project: Project, baseline: Baseline | None, phase_id: int | None = None
) -> dict[int, SpecVersion]:
    """spec_id → 版本。baseline 是 None 時表示目前最新版（phase_id 有值時只看那個階段）。"""
    if baseline is not None:
        rows = s.execute(
            select(BaselineItem.spec_id, SpecVersion)
            .join(SpecVersion, SpecVersion.id == BaselineItem.version_id)
            .where(BaselineItem.baseline_id == baseline.id)
        ).all()
        return {spec_id: version for spec_id, version in rows}
    snap = {}
    for spec in list_specs(s, project.id, phase_id=phase_id):
        head = get_head(s, spec)
        if head is not None:
            snap[spec.id] = head
    return snap


def compare(
    s: Session, project: Project, base: Baseline | None, target: Baseline | None
) -> list[dict[str, Any]]:
    """比較兩個時間點。target 是 None 表示跟目前最新版比。"""
    # 只收某個階段的基準版本跟目前比時，目前這邊也只看同一個階段，不然其他階段會全部變成「新增」
    scope = base.phase_id if base is not None and target is None else None
    old = _snapshot(s, project, base)
    new = _snapshot(s, project, target, phase_id=scope)
    specs = {sp.id: sp for sp in s.scalars(select(Spec).where(Spec.id.in_(set(old) | set(new))))}
    phases = {ph.id: ph for ph in project.phases}
    result = []
    for spec_id in set(old) | set(new):
        spec = specs[spec_id]
        a, b = old.get(spec_id), new.get(spec_id)
        if a is None:
            status = "added"
        elif b is None:
            status = "removed"
        elif a.content_hash == b.content_hash:
            status = "unchanged"
        else:
            status = "changed"
        phase = phases.get(spec.phase_id)
        result.append(
            {
                "spec_id": spec_id,
                "title": spec.title,
                "phase_name": phase.name if phase else "",
                "phase_order": phase.sort_order if phase else 0,
                "status": status,
                "from_version": a.version_no if a else None,
                "to_version": b.version_no if b else None,
            }
        )
    order = {"changed": 0, "added": 1, "removed": 2, "unchanged": 3}
    result.sort(key=lambda r: (order[r["status"]], r["phase_order"], r["title"]))
    return result


def export_zip(s: Session, project: Project, baseline: Baseline | None = None) -> bytes:
    snap = _snapshot(s, project, baseline)
    specs = {sp.id: sp for sp in s.scalars(select(Spec).where(Spec.id.in_(snap.keys())))}
    phases = {ph.id: ph for ph in project.phases}
    entries = []
    for spec_id, version in snap.items():
        spec = specs[spec_id]
        phase = phases.get(spec.phase_id)
        entries.append(
            ExportEntry(
                spec_id=spec.id,
                phase=phase.name if phase else "未分類",
                title=spec.title,
                status=spec.status,
                version_no=version.version_no,
                label=version.label,
                content=version.content,
            )
        )
    entries.sort(key=lambda e: (e.phase, e.title))
    extra = {"baseline": baseline.name if baseline else None}
    return build_zip(project.name, entries, extra)
