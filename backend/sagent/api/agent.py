"""AI 代理：範本、模型清單、建立/查詢/取消工作、確認大綱後繼續、即時進度（SSE）。"""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import asdict
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.doc_templates import CATEGORIES
from ..agents.llm import ollama_list_models
from ..agents.planner import parse_terms
from ..app_state import AppState
from ..db import utcnow
from ..models import AgentEvent, AgentRun, Spec
from ..schemas import EventOut, RunContinue, RunCreate, RunDetail, RunOut
from ..services import specs as spec_service
from ..services.diagram_snippets import SNIPPETS
from ..services.errors import BadRequest, Conflict, NotFound, Unavailable
from .deps import get_state

router = APIRouter(prefix="/api/agent", tags=["agent"])

FINAL_STATUSES = ("succeeded", "failed", "cancelled")
RUN_END_TEXT = {"succeeded": "完成", "failed": "失敗", "cancelled": "已取消"}


@router.get("/templates")
def list_templates(state: AppState = Depends(get_state)) -> list[dict[str, Any]]:
    return [t.to_dict() for t in state.templates().values()]


@router.get("/template-categories")
def list_template_categories() -> list[dict[str, str]]:
    """類型：新增 Spec 時先選類型，再選這個類型的文件類型。"""
    return [{"key": c.key, "name": c.name, "description": c.description} for c in CATEGORIES]


@router.get("/diagram-kinds")
def list_diagram_kinds() -> list[dict[str, str]]:
    """編輯器「插入圖表」選單的圖表骨架。plantuml 是空字串表示 PlantUML 沒有這種圖。"""
    return [asdict(s) for s in SNIPPETS]


@router.get("/models")
async def list_models(state: AppState = Depends(get_state)) -> dict[str, Any]:
    current = await run_in_threadpool(state.settings)
    if state.cfg.llm_provider == "fake":
        return {
            "provider": "fake",
            "default_model": current.default_model,
            "models": [
                {"name": current.default_model, "parameter_size": "示範", "capabilities": ["completion"]}
            ],
        }
    try:
        models = await ollama_list_models(current.ollama_url)
    except httpx.HTTPError as exc:
        raise Unavailable(f"連不到 Ollama（{current.ollama_url}）：{exc}") from exc
    return {"provider": "ollama", "default_model": current.default_model, "models": models}


def _event_out(e: AgentEvent) -> dict[str, Any]:
    return EventOut(
        seq=e.seq,
        type=e.type,
        agent=e.agent,
        message=e.message,
        data=e.data or {},
        ts=e.created_at.isoformat().replace("+00:00", "Z"),
    ).model_dump()


def run_out(s: Session, run: AgentRun, state: AppState) -> RunOut:
    spec = s.get(Spec, run.spec_id)
    return RunOut.model_validate(run).model_copy(
        update={
            "spec_title": spec.title if spec else "",
            "queue_position": state.runner.queue_position(run.id) if run.status == "queued" else None,
        }
    )


@router.post("/runs", response_model=RunOut, status_code=201)
async def create_run(body: RunCreate, state: AppState = Depends(get_state)):
    if not body.requirement.strip():
        raise BadRequest("請輸入需求內容")
    current = await run_in_threadpool(state.settings)
    templates = state.templates()

    def _create() -> RunOut:
        with state.db() as s:
            spec = spec_service.get_spec(s, body.spec_id)
            if spec.archived:
                raise BadRequest("已封存的 Spec 不能使用 AI 撰寫")
            if spec_service.active_run_for_spec(s, spec.id):
                raise Conflict("這份 Spec 已經有 AI 工作在執行或排隊中")
            doc_type = body.doc_type or spec.doc_type
            if doc_type not in templates:
                raise BadRequest(f"沒有「{doc_type}」這種文件範本")
            o = body.options
            options = {
                "uml_syntax": o.uml_syntax or current.uml_syntax,
                "max_revisions": current.max_revisions if o.max_revisions is None else o.max_revisions,
                "temperature": current.temperature if o.temperature is None else o.temperature,
                "diagrams": (current.diagrams if o.diagrams is None else o.diagrams)
                and templates[doc_type].allow_diagrams,
                "max_diagrams": 4 if o.max_diagrams is None else o.max_diagrams,
                "plan_first": bool(o.plan_first) and body.mode == "draft",
                "ollama_url": current.ollama_url,
                "num_ctx": current.num_ctx,
            }
            if doc_type != spec.doc_type:
                spec.doc_type = doc_type
            run = AgentRun(
                project_id=spec.project_id,
                spec_id=spec.id,
                mode=body.mode,
                status="queued",
                model=(o.model or current.default_model).strip(),
                doc_type=doc_type,
                requirement=body.requirement.strip(),
                options=options,
                base_version_no=spec.head_version_no,
            )
            s.add(run)
            s.flush()
            return run_out(s, run, state)

    out = await run_in_threadpool(_create)
    state.runner.submit(out.id)
    return out.model_copy(update={"queue_position": state.runner.queue_position(out.id)})


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    project_id: int | None = None,
    spec_id: int | None = None,
    limit: int = 50,
    state: AppState = Depends(get_state),
):
    with state.db() as s:
        stmt = select(AgentRun).order_by(AgentRun.id.desc()).limit(min(max(limit, 1), 200))
        if project_id is not None:
            stmt = stmt.where(AgentRun.project_id == project_id)
        if spec_id is not None:
            stmt = stmt.where(AgentRun.spec_id == spec_id)
        return [run_out(s, r, state) for r in s.scalars(stmt)]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: int, state: AppState = Depends(get_state)):
    with state.db() as s:
        run = s.get(AgentRun, run_id)
        if run is None:
            raise NotFound("找不到這個 AI 工作")
        events = s.scalars(select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.seq))
        return RunDetail.model_validate(
            {
                **run_out(s, run, state).model_dump(),
                "plan": run.plan,
                "review": run.review,
                "events": [_event_out(e) for e in events],
            }
        )


@router.post("/runs/{run_id}/continue", response_model=RunOut)
async def continue_run(run_id: int, body: RunContinue, state: AppState = Depends(get_state)):
    """先確認大綱的工作：帶入待確認問題的回答、改過的名詞表和要點，重新排隊繼續撰寫。"""

    def _update() -> tuple[RunOut, int, dict[str, Any]]:
        with state.db() as s:
            run = s.get(AgentRun, run_id)
            if run is None:
                raise NotFound("找不到這個 AI 工作")
            if run.status != "planned":
                raise Conflict("這個 AI 工作不是在等待確認大綱")
            plan = copy.deepcopy(run.plan or {})
            questions = [str(q) for q in plan.get("open_questions") or []]
            given = {a.question.strip(): a.answer.strip() for a in body.answers if a.answer.strip()}
            answered = [(q, given[q.strip()]) for q in questions if q.strip() in given]
            plan["open_questions"] = [q for q in questions if q.strip() not in given]
            plan["answered"] = [list(qa) for qa in answered]
            if body.terms is not None:
                plan["terms"] = [asdict(t) for t in parse_terms([t.model_dump() for t in body.terms], limit=20)]
            points = {sp.id: [p.strip()[:200] for p in sp.key_points if p.strip()] for sp in body.sections}
            for sec in plan.get("sections") or []:
                if sec.get("id") in points:
                    sec["key_points"] = points[sec["id"]]
            run.plan = plan
            run.options = {
                **(run.options or {}),
                "plan_confirmed": True,
                "answers": [{"question": q, "answer": a} for q, a in answered],
            }
            run.status = "queued"
            s.flush()
            return run_out(s, run, state), len(answered), plan

    out, n, plan = await run_in_threadpool(_update)
    # 畫面上的大綱換成使用者確認過的版本（Planner 那一步記的是原本的）
    await state.runner.publish(run_id, "plan_update", "system", "", {"plan": plan})
    message = f"已確認大綱（回答 {n} 個待確認問題），繼續撰寫" if n else "已確認大綱，繼續撰寫"
    await state.runner.publish(run_id, "log", "system", message, {"level": "info"})
    state.runner.submit(run_id)
    return out.model_copy(update={"queue_position": state.runner.queue_position(run_id)})


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int, state: AppState = Depends(get_state)) -> dict[str, bool]:
    return {"cancelled": await state.runner.cancel(run_id)}


def _sse(event: dict[str, Any]) -> str:
    head = f"id: {event['seq']}\n" if event.get("seq") is not None else ""
    return f"{head}data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/runs/{run_id}/events")
async def run_events(run_id: int, request: Request, after: int = 0, state: AppState = Depends(get_state)):
    """即時進度。先補送資料庫裡 seq > after 的事件，再轉送新事件，直到 run_end。"""
    last_id = request.headers.get("last-event-id", "")
    if last_id.isdigit():
        after = max(after, int(last_id))

    def _load() -> tuple[str, bool, list[dict[str, Any]]]:
        with state.db() as s:
            run = s.get(AgentRun, run_id)
            if run is None:
                raise NotFound("找不到這個 AI 工作")
            events = [
                e
                for e in s.scalars(
                    select(AgentEvent)
                    .where(AgentEvent.run_id == run_id, AgentEvent.seq > after)
                    .order_by(AgentEvent.seq)
                )
                if e.type != "run_paused" or run.status == "planned"
            ]
            just_finished = run.finished_at is not None and (utcnow() - run.finished_at).total_seconds() < 10
            return run.status, just_finished, [_event_out(e) for e in events]

    def _final_event() -> dict[str, Any]:
        """資料庫裡沒有 run_end 事件時（例如伺服器重啟時中斷），用工作的結果補一個。"""
        with state.db() as s:
            run = s.get(AgentRun, run_id)
            data = {"status": run.status, "version_no": run.result_version_no, "error": run.error}
            message = run.error or RUN_END_TEXT.get(run.status, "")
            return {"type": "run_end", "agent": "system", "message": message, "data": data}

    queue = state.runner.subscribe(run_id)  # 先訂閱再讀歷史，避免漏掉中間的事件
    try:
        status, just_finished, past = await run_in_threadpool(_load)
    except Exception:
        state.runner.unsubscribe(run_id, queue)
        raise

    async def stream():
        last = after
        try:
            yield "retry: 3000\n\n"
            for event in past:
                last = event["seq"]
                yield _sse(event)
            if status == "planned":
                return  # 等使用者確認大綱；繼續之後畫面會重新連線
            if status in FINAL_STATUSES:
                if past and past[-1]["type"] == "run_end":
                    return
                # 狀態已經結束，但 run_end 事件可能正在寫入：剛結束的工作等一下，等不到再用資料庫的結果補
                loop = asyncio.get_running_loop()
                deadline = loop.time() + (3 if just_finished else 0)
                while (remaining := deadline - loop.time()) > 0:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=remaining)
                    except TimeoutError:
                        break
                    if event.get("seq") is not None and event["seq"] <= last:
                        continue
                    yield _sse(event)
                    if event["type"] == "run_end":
                        return
                yield _sse(await run_in_threadpool(_final_event))
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": keep-alive\n\n"
                    continue
                seq = event.get("seq")
                if seq is not None:
                    if seq <= last:
                        continue
                    last = seq
                yield _sse(event)
                if event["type"] in ("run_end", "run_paused"):
                    return
        finally:
            state.runner.unsubscribe(run_id, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
