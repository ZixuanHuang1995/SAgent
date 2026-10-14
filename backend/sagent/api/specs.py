"""單一 Spec：內容、存檔、版本歷程、差異比較、還原、標籤、匯出。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..app_state import AppState
from ..models import Spec
from ..schemas import (
    ContentSave,
    LabelIn,
    RestoreIn,
    SaveResult,
    SpecDetail,
    SpecOut,
    SpecUpdate,
    VersionDetail,
    VersionMeta,
)
from ..services import specs as spec_service
from ..services.errors import BadRequest
from ..services.export import safe_filename
from .deps import download, get_state

router = APIRouter(prefix="/api/specs", tags=["specs"])


def spec_detail(s: Session, spec: Spec) -> SpecDetail:
    head = spec_service.get_head(s, spec)
    run = spec_service.active_run_for_spec(s, spec.id)
    return SpecDetail.model_validate(
        {
            **SpecOut.model_validate(spec).model_dump(),
            "content": head.content if head else "",
            "head": VersionMeta.model_validate(head) if head else None,
            "active_run_id": run.id if run else None,
        }
    )


@router.get("/{spec_id}", response_model=SpecDetail)
def get_spec(spec_id: int, state: AppState = Depends(get_state)):
    with state.db() as s:
        return spec_detail(s, spec_service.get_spec(s, spec_id))


@router.patch("/{spec_id}", response_model=SpecOut)
def update_spec(spec_id: int, body: SpecUpdate, state: AppState = Depends(get_state)):
    with state.db() as s:
        spec = spec_service.get_spec(s, spec_id)
        return SpecOut.model_validate(spec_service.update_spec(s, spec, **body.model_dump()))


@router.put("/{spec_id}/content", response_model=SaveResult)
def save_content(spec_id: int, body: ContentSave, state: AppState = Depends(get_state)):
    with state.db() as s:
        spec = spec_service.get_spec(s, spec_id)
        if spec.archived:
            raise BadRequest("已封存的 Spec 不能修改，請先取消封存")
        version, created = spec_service.add_version(
            s, spec, body.content, message=body.message, source="manual", base_version_no=body.base_version_no
        )
        return SaveResult(
            created=created, version=VersionMeta.model_validate(version), spec=SpecOut.model_validate(spec)
        )


@router.post("/{spec_id}/archive", response_model=SpecOut)
def archive_spec(spec_id: int, state: AppState = Depends(get_state)):
    with state.db() as s:
        return SpecOut.model_validate(spec_service.set_archived(s, spec_service.get_spec(s, spec_id), True))


@router.post("/{spec_id}/unarchive", response_model=SpecOut)
def unarchive_spec(spec_id: int, state: AppState = Depends(get_state)):
    with state.db() as s:
        return SpecOut.model_validate(spec_service.set_archived(s, spec_service.get_spec(s, spec_id), False))


@router.get("/{spec_id}/versions", response_model=list[VersionMeta])
def list_versions(spec_id: int, state: AppState = Depends(get_state)):
    with state.db() as s:
        spec = spec_service.get_spec(s, spec_id)
        return [VersionMeta.model_validate(v) for v in spec_service.list_versions(s, spec)]


@router.get("/{spec_id}/versions/{version_no}", response_model=VersionDetail)
def get_version(spec_id: int, version_no: int, state: AppState = Depends(get_state)):
    with state.db() as s:
        spec = spec_service.get_spec(s, spec_id)
        return VersionDetail.model_validate(spec_service.get_version(s, spec, version_no))


@router.post("/{spec_id}/versions/{version_no}/restore", response_model=SaveResult)
def restore_version(spec_id: int, version_no: int, body: RestoreIn, state: AppState = Depends(get_state)):
    with state.db() as s:
        spec = spec_service.get_spec(s, spec_id)
        if spec.archived:
            raise BadRequest("已封存的 Spec 不能修改，請先取消封存")
        version, created = spec_service.restore_version(s, spec, version_no, body.message)
        return SaveResult(
            created=created, version=VersionMeta.model_validate(version), spec=SpecOut.model_validate(spec)
        )


@router.put("/{spec_id}/versions/{version_no}/label", response_model=VersionMeta)
def set_label(spec_id: int, version_no: int, body: LabelIn, state: AppState = Depends(get_state)):
    with state.db() as s:
        spec = spec_service.get_spec(s, spec_id)
        return VersionMeta.model_validate(spec_service.set_label(s, spec, version_no, body.label))


@router.get("/{spec_id}/diff")
def diff(
    spec_id: int,
    from_version: int | None = None,
    to_version: int | None = None,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """比較兩個版本。沒指定 to 時用最新版，沒指定 from 時用 to 的前一版。"""
    with state.db() as s:
        spec = spec_service.get_spec(s, spec_id)
        to_no = to_version or spec.head_version_no
        if to_no <= 0:
            raise BadRequest("這份 Spec 還沒有版本")
        from_no = from_version if from_version is not None else to_no - 1
        new = spec_service.get_version(s, spec, to_no).content
        old = spec_service.get_version(s, spec, from_no).content if from_no > 0 else ""
        return {"from_version": from_no, "to_version": to_no, **spec_service.diff_texts(old, new)}


@router.get("/{spec_id}/export")
def export_spec(spec_id: int, version: int | None = None, state: AppState = Depends(get_state)):
    with state.db() as s:
        spec = spec_service.get_spec(s, spec_id)
        no = version or spec.head_version_no
        if no <= 0:
            raise BadRequest("這份 Spec 還沒有版本")
        content = spec_service.get_version(s, spec, no).content
        return download(content, f"{safe_filename(spec.title)}-v{no}.md", "text/markdown; charset=utf-8")
