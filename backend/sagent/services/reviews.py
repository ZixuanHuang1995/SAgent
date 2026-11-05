"""AI 審查結果的處理進度。

每份 Spec 看最近一次成功的 AI 工作：它最後一次審查列出的格式、邏輯錯誤和人工覆核項目，
使用者可以逐項標成「已處理」。項目用位置當代號（"issue:0"、"point:3"），
審查結果存進資料庫後就不會再改，所以位置不會變。

處理過的項目另外記怎麼處理的（review_labels）：已處理（真的有問題、改好或確認了）、誤報（審查說錯了）、
略過（先不處理）。累積下來就能算 Reviewer 的精確率，之後改提示詞、優化時當成人工標註。
舊資料只有「已處理」，沒有記錄的一律當成 fixed。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents.reviewer import ITEM_GAP_REASON, NUMBER_GAP_REASON
from ..models import AgentRun
from .errors import BadRequest, Conflict, NotFound

# 人工覆核項目的分組和排序（C-11）：需求追溯的缺口 → 模型列的 → 文件自己標的（假設）（待確認）
POINT_GROUPS = ("trace", "model", "marked")
VERDICTS = ("yes", "partial", "no")


def final_review(run: AgentRun) -> dict[str, Any] | None:
    return (run.review or {}).get("final") or None


def point_group(point: dict[str, Any]) -> str:
    """trace：需求有、文件裡找不到（requirement_gaps）；model：審查模型列的；marked：文件自己標的。"""
    if point.get("reason") in (NUMBER_GAP_REASON, ITEM_GAP_REASON):
        return "trace"
    return "model" if point.get("source") == "llm" else "marked"


def with_groups(review: dict[str, Any] | None) -> dict[str, Any] | None:
    """給畫面用：最後一次審查的人工覆核項目加上 group。不改資料庫裡存的內容，項目的位置也不變。"""
    if not review or not isinstance(review.get("final"), dict):
        return review
    final = dict(review["final"])
    final["review_points"] = [{**p, "group": point_group(p)} for p in final.get("review_points") or []]
    return {**review, "final": final}


def set_verdict(s: Session, run_id: int, verdict: str | None, note: str) -> AgentRun:
    """使用者判斷這份 AI 版可不可以直接交付（C-10）。只有成功的工作有 AI 版可以判斷。"""
    run = s.get(AgentRun, run_id)
    if run is None:
        raise NotFound("找不到這個 AI 工作")
    if run.status != "succeeded":
        raise Conflict("只有完成的 AI 工作可以判斷能不能交付")
    if verdict is not None and verdict not in VERDICTS:
        raise BadRequest("能不能交付只能選可以、要小改或不行")
    run.human_verdict = verdict
    run.human_verdict_note = note.strip() if verdict is not None else ""
    return run


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


LABELS = ("fixed", "false", "skip")


def set_checked(
    s: Session, run_id: int, key: str, done: bool, label: str | None = None
) -> tuple[list[str], dict[str, str]]:
    """done 標成處理過（或取消）。label 是怎麼處理的，沒給就是 fixed（舊的前端只送 done）。"""
    run = s.get(AgentRun, run_id)
    if run is None:
        raise NotFound("找不到這個 AI 工作")
    review = final_review(run)
    keys = review_keys(review) if review else []
    if key not in keys:
        raise BadRequest("這次審查沒有這個項目")
    if label is not None and label not in LABELS:
        raise BadRequest("處理結果只能是已處理、誤報或略過")
    checked = set(run.review_checked or [])
    labels = dict(run.review_labels or {})
    if done:
        checked.add(key)
        labels[key] = label or "fixed"
    else:
        checked.discard(key)
        labels.pop(key, None)
    # 照審查裡的順序存，結果固定
    run.review_checked = [k for k in keys if k in checked]
    run.review_labels = {k: labels[k] for k in keys if k in labels and k in checked}
    return run.review_checked, run.review_labels
