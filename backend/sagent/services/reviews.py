"""AI 審查結果的處理進度。

每份 Spec 看最近一次成功的 AI 工作：它最後一次審查列出的格式、邏輯錯誤和人工覆核項目，
使用者可以逐項標成「已處理」。項目用位置當代號（"issue:0"、"point:3"），
審查結果存進資料庫後就不會再改，所以位置不會變。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AgentRun
from .errors import BadRequest, NotFound


def final_review(run: AgentRun) -> dict[str, Any] | None:
    return (run.review or {}).get("final") or None


def review_keys(review: dict[str, Any]) -> list[str]:
    issues = review.get("issues") or []
    points = review.get("review_points") or []
    return [f"issue:{i}" for i in range(len(issues))] + [f"point:{i}" for i in range(len(points))]


def latest_runs(s: Session, spec_ids: list[int]) -> dict[int, AgentRun]:
    """每份 Spec 最近一次成功的 AI 工作。"""
    if not spec_ids:
        return {}
    latest = (
        select(func.max(AgentRun.id))
        .where(AgentRun.spec_id.in_(spec_ids), AgentRun.status == "succeeded")
        .group_by(AgentRun.spec_id)
    )
    return {r.spec_id: r for r in s.scalars(select(AgentRun).where(AgentRun.id.in_(latest)))}


def summary(run: AgentRun | None) -> dict[str, Any] | None:
    """審查項目總數和還沒處理的數量。沒有審查、或審查沒列出任何項目時是 None。"""
    review = final_review(run) if run else None
    if not review:
        return None
    keys = review_keys(review)
    if not keys:
        return None
    done = set(run.review_checked or []) & set(keys)
    return {
        "run_id": run.id,
        "version_no": run.result_version_no,
        "total": len(keys),
        "open": len(keys) - len(done),
    }


def summaries(s: Session, spec_ids: list[int]) -> dict[int, dict[str, Any] | None]:
    runs = latest_runs(s, spec_ids)
    return {spec_id: summary(runs.get(spec_id)) for spec_id in spec_ids}


def set_checked(s: Session, run_id: int, key: str, done: bool) -> list[str]:
    run = s.get(AgentRun, run_id)
    if run is None:
        raise NotFound("找不到這個 AI 工作")
    review = final_review(run)
    keys = review_keys(review) if review else []
    if key not in keys:
        raise BadRequest("這次審查沒有這個項目")
    checked = set(run.review_checked or [])
    if done:
        checked.add(key)
    else:
        checked.discard(key)
    # 照審查裡的順序存，結果固定
    run.review_checked = [k for k in keys if k in checked]
    return run.review_checked
