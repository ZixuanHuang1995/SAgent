"""專案、階段、Spec 清單、基準版本、匯出。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents.doc_templates import get_template
from ..app_state import AppState
from ..models import BaselineItem, Project
from ..schemas import (
    BaselineCreate,
    BaselineOut,
    PhaseCreate,
    PhaseOut,
    PhaseReorder,
    PhaseUpdate,
    ProjectCreate,
    ProjectOut,
    ProjectTree,
    ProjectUpdate,
    SpecCreate,
    SpecDetail,
    SpecOut,
)
from ..services import baselines as baseline_service
from ..services import specs as spec_service
from ..services.errors import BadRequest
from ..services.export import safe_filename
from .deps import download, get_state
from .specs import spec_detail

router = APIRouter(prefix="/api", tags=["projects"])


def project_out(s: Session, project: Project) -> ProjectOut:
    phases, specs = spec_service.project_counts(s, project.id)
    return ProjectOut.model_validate(project).model_copy(update={"phase_count": phases, "spec_count": specs})


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(state: AppState = Depends(get_state)):
    with state.db() as s:
        projects = s.scalars(select(Project).order_by(Project.updated_at.desc())).all()
        return [project_out(s, p) for p in projects]


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, state: AppState = Depends(get_state)):
    with state.db() as s:
        project = spec_service.create_project(s, body.name, body.code, body.description, body.phases)
        return project_out(s, project)


@router.get("/projects/{project_id}", response_model=ProjectTree)
def project_tree(project_id: int, archived: bool = False, state: AppState = Depends(get_state)):
    with state.db() as s:
        project = spec_service.get_project(s, project_id)
        return ProjectTree(
            project=project_out(s, project),
            phases=[PhaseOut.model_validate(ph) for ph in project.phases],
            specs=[
                SpecOut.model_validate(sp) for sp in spec_service.list_specs(s, project.id, archived=archived)
            ],
        )


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, body: ProjectUpdate, state: AppState = Depends(get_state)):
    with state.db() as s:
        project = spec_service.get_project(s, project_id)
        spec_service.update_project(s, project, **body.model_dump())
        return project_out(s, project)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, state: AppState = Depends(get_state)):
    with state.db() as s:
        spec_service.delete_project(s, spec_service.get_project(s, project_id))
    return Response(status_code=204)


# --- 階段 ---


@router.post("/projects/{project_id}/phases", response_model=PhaseOut, status_code=201)
def create_phase(project_id: int, body: PhaseCreate, state: AppState = Depends(get_state)):
    with state.db() as s:
        project = spec_service.get_project(s, project_id)
        return PhaseOut.model_validate(spec_service.create_phase(s, project, body.name, body.description))


@router.post("/projects/{project_id}/phases/reorder", status_code=204)
def reorder_phases(project_id: int, body: PhaseReorder, state: AppState = Depends(get_state)):
    with state.db() as s:
        spec_service.reorder_phases(s, spec_service.get_project(s, project_id), body.phase_ids)
    return Response(status_code=204)


@router.patch("/phases/{phase_id}", response_model=PhaseOut)
def update_phase(phase_id: int, body: PhaseUpdate, state: AppState = Depends(get_state)):
    with state.db() as s:
        phase = spec_service.get_phase(s, phase_id)
        return PhaseOut.model_validate(spec_service.update_phase(s, phase, **body.model_dump()))


@router.delete("/phases/{phase_id}", status_code=204)
def delete_phase(phase_id: int, state: AppState = Depends(get_state)):
    with state.db() as s:
        spec_service.delete_phase(s, spec_service.get_phase(s, phase_id))
    return Response(status_code=204)


# --- Spec 清單與建立 ---


@router.get("/projects/{project_id}/specs", response_model=list[SpecOut])
def list_specs(
    project_id: int,
    q: str = "",
    phase_id: int | None = None,
    status: str | None = None,
    archived: bool = False,
    state: AppState = Depends(get_state),
):
    with state.db() as s:
        spec_service.get_project(s, project_id)
        specs = spec_service.list_specs(
            s, project_id, phase_id=phase_id, archived=archived, q=q, status=status
        )
        return [SpecOut.model_validate(sp) for sp in specs]


@router.post("/projects/{project_id}/specs", response_model=SpecDetail, status_code=201)
def create_spec(project_id: int, body: SpecCreate, state: AppState = Depends(get_state)):
    templates = state.templates()
    with state.db() as s:
        project = spec_service.get_project(s, project_id)
        content, source, message = None, "manual", ""
        if body.init == "template":
            template = get_template(templates, body.doc_type)
            content, source, message = (
                template.skeleton(body.title.strip()),
                "template",
                f"依「{template.name}」範本建立",
            )
        elif body.init == "blank":
            content, message = f"# {body.title.strip()}\n", "建立文件"
        elif body.init == "content":
            content, source, message = body.content or "", "import", "匯入文件"
        spec = spec_service.create_spec(
            s,
            project,
            phase_id=body.phase_id,
            title=body.title,
            doc_type=body.doc_type,
            content=content,
            source=source,
            message=message,
        )
        return spec_detail(s, spec)


# --- 基準版本 ---


def baseline_out(s: Session, baseline) -> BaselineOut:
    count = s.scalar(select(func.count(BaselineItem.id)).where(BaselineItem.baseline_id == baseline.id)) or 0
    return BaselineOut.model_validate(baseline).model_copy(update={"item_count": count})


@router.get("/projects/{project_id}/baselines", response_model=list[BaselineOut])
def list_baselines(project_id: int, state: AppState = Depends(get_state)):
    with state.db() as s:
        project = spec_service.get_project(s, project_id)
        return [baseline_out(s, b) for b in baseline_service.list_baselines(s, project)]


@router.post("/projects/{project_id}/baselines", response_model=BaselineOut, status_code=201)
def create_baseline(project_id: int, body: BaselineCreate, state: AppState = Depends(get_state)):
    with state.db() as s:
        project = spec_service.get_project(s, project_id)
        baseline = baseline_service.create_baseline(s, project, body.name, body.note, body.phase_id)
        return baseline_out(s, baseline)


@router.get("/baselines/{baseline_id}")
def get_baseline(baseline_id: int, state: AppState = Depends(get_state)) -> dict[str, Any]:
    with state.db() as s:
        baseline = baseline_service.get_baseline(s, baseline_id)
        return {
            "baseline": baseline_out(s, baseline).model_dump(mode="json"),
            "items": baseline_service.baseline_items(s, baseline),
        }


@router.delete("/baselines/{baseline_id}", status_code=204)
def delete_baseline(baseline_id: int, state: AppState = Depends(get_state)):
    with state.db() as s:
        baseline_service.delete_baseline(s, baseline_service.get_baseline(s, baseline_id))
    return Response(status_code=204)


@router.get("/projects/{project_id}/compare")
def compare(
    project_id: int,
    from_baseline: int | None = None,
    to_baseline: int | None = None,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """比較兩個基準版本；沒給 to_baseline 時跟目前最新版比，沒給 from_baseline 時從空白開始比。"""
    with state.db() as s:
        project = spec_service.get_project(s, project_id)
        base = baseline_service.get_baseline(s, from_baseline) if from_baseline else None
        target = baseline_service.get_baseline(s, to_baseline) if to_baseline else None
        for b in (base, target):
            if b is not None and b.project_id != project.id:
                raise BadRequest("基準版本不屬於這個專案")
        items = baseline_service.compare(s, project, base, target)
        return {
            "from": base.name if base else None,
            "to": target.name if target else "目前最新版",
            "items": items,
        }


@router.get("/projects/{project_id}/export")
def export_project(project_id: int, baseline_id: int | None = None, state: AppState = Depends(get_state)):
    with state.db() as s:
        project = spec_service.get_project(s, project_id)
        baseline = baseline_service.get_baseline(s, baseline_id) if baseline_id else None
        if baseline is not None and baseline.project_id != project.id:
            raise BadRequest("基準版本不屬於這個專案")
        data = baseline_service.export_zip(s, project, baseline)
        suffix = safe_filename(baseline.name) if baseline else "latest"
        return download(data, f"{safe_filename(project.name)}-{suffix}.zip", "application/zip")
