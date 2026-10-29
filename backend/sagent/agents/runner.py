"""AI 工作的背景執行器。

- 工作排隊，一次只跑一個（CPU 推論同時跑多個只會更慢）
- 執行過程的事件存進資料庫，並即時推給正在看的畫面（SSE）
- 逐字串流不存資料庫，只即時推送，每 0.15 秒合併送一次
- 伺服器重啟時：排隊中的工作重新排隊，執行到一半的標成失敗
- 先確認大綱的工作：規劃完存成 planned 停下來（不占佇列），使用者確認後重新排隊，沿用存好的大綱繼續
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..db import session_scope, utcnow
from ..models import AgentEvent, AgentRun, Phase, Project, Spec
from ..services import specs as spec_service
from .doc_templates import DocTemplate, get_template
from .llm import LLMClient
from .pipeline import SpecPipeline
from .planner import GLOSSARY_LIMIT, parse_terms
from .types import AgentStats, PipelineOptions, Plan, RunInput

log = logging.getLogger("sagent.runner")

TOKEN_FLUSH_SECONDS = 0.15


class RunEmitter:
    def __init__(self, manager: RunManager, run_id: int):
        self.manager = manager
        self.run_id = run_id
        self._buf: dict[tuple[str, str], list[str]] = {}
        self._last_flush = time.monotonic()

    async def emit(
        self, type: str, agent: str, message: str = "", data: dict[str, Any] | None = None
    ) -> None:
        self.flush_tokens()
        await self.manager.publish(self.run_id, type, agent, message, data or {})

    def token(self, agent: str, step: str, text: str) -> None:
        self._buf.setdefault((agent, step), []).append(text)
        if time.monotonic() - self._last_flush >= TOKEN_FLUSH_SECONDS:
            self.flush_tokens()

    def flush_tokens(self) -> None:
        self._last_flush = time.monotonic()
        if not self._buf:
            return
        buf, self._buf = self._buf, {}
        for (agent, step), parts in buf.items():
            self.manager.broadcast(
                self.run_id, {"type": "token", "agent": agent, "data": {"step": step, "text": "".join(parts)}}
            )


LLMFactory = Callable[[str, dict[str, Any]], LLMClient]


class RunManager:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        llm_factory: LLMFactory,
        templates_provider: Callable[[], dict[str, DocTemplate]],
        plantuml_available: Callable[[], bool],
    ):
        self.session_factory = session_factory
        self.llm_factory = llm_factory
        self.templates_provider = templates_provider
        self.plantuml_available = plantuml_available
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._tasks: dict[int, asyncio.Task] = {}
        self._subscribers: dict[int, set[asyncio.Queue]] = defaultdict(set)
        self._seq: dict[int, int] = {}
        self._idle = asyncio.Event()
        self._idle.set()

    # --- 生命週期 ---

    async def start(self) -> None:
        with session_scope(self.session_factory) as s:
            for run in s.scalars(select(AgentRun).where(AgentRun.status == "running")):
                run.status = "failed"
                run.error = "伺服器重新啟動，工作被中斷"
                run.finished_at = utcnow()
            queued = list(
                s.scalars(select(AgentRun.id).where(AgentRun.status == "queued").order_by(AgentRun.id))
            )
        for run_id in queued:
            self._queue.put_nowait(run_id)
        self._worker = asyncio.create_task(self._work_loop(), name="sagent-run-worker")

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:  # 等執行中的工作把「已取消」寫回資料庫
            await asyncio.wait(tasks, timeout=5)
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

    async def wait_idle(self, timeout: float = 30) -> None:
        """測試用：等到佇列清空。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.empty() and not self._tasks:
                return
            await asyncio.sleep(0.02)
        raise TimeoutError("等待 AI 工作逾時")

    def submit(self, run_id: int) -> None:
        self._queue.put_nowait(run_id)

    def queue_position(self, run_id: int) -> int | None:
        items = list(self._queue._queue)  # type: ignore[attr-defined]
        return items.index(run_id) + 1 if run_id in items else None

    async def cancel(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
            return True
        try:  # 還在排隊：從佇列拿掉，排隊位置才不會算到它
            self._queue._queue.remove(run_id)  # type: ignore[attr-defined]
        except ValueError:
            pass
        return await self._mark_cancelled(run_id, statuses=("queued", "planned"))

    async def _mark_cancelled(
        self,
        run_id: int,
        statuses: tuple[str, ...] = ("queued", "running"),
        pipeline: SpecPipeline | None = None,
    ) -> bool:
        """狀態還在 statuses 裡才改成已取消，並送出 run_end。已經完成或失敗的不會被蓋掉。"""

        def _update() -> bool:
            with session_scope(self.session_factory) as s:
                run = s.get(AgentRun, run_id)
                if run is None or run.status not in statuses:
                    return False
                run.status = "cancelled"
                run.finished_at = utcnow()
                run.error = "使用者取消"
                if pipeline is not None:
                    run.stats = {k: asdict(v) for k, v in pipeline.stats.items()}
                return True

        if not await asyncio.to_thread(_update):
            return False
        await self.publish(run_id, "run_end", "system", "已取消", {"status": "cancelled"})
        return True

    # --- 事件 ---

    def subscribe(self, run_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers[run_id].add(q)
        return q

    def unsubscribe(self, run_id: int, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(run_id)
        if subs is not None:
            subs.discard(q)
            if not subs:
                self._subscribers.pop(run_id, None)

    def broadcast(self, run_id: int, event: dict[str, Any]) -> None:
        for q in list(self._subscribers.get(run_id, ())):
            q.put_nowait(event)

    def _next_seq(self, run_id: int) -> int:
        if run_id not in self._seq:
            with session_scope(self.session_factory) as s:
                self._seq[run_id] = (
                    s.scalar(select(func.max(AgentEvent.seq)).where(AgentEvent.run_id == run_id)) or 0
                )
        self._seq[run_id] += 1
        return self._seq[run_id]

    async def publish(self, run_id: int, type: str, agent: str, message: str, data: dict[str, Any]) -> None:
        seq = self._next_seq(run_id)
        now = datetime.now(UTC)
        event = {
            "seq": seq,
            "type": type,
            "agent": agent,
            "message": message,
            "data": data,
            "ts": now.isoformat().replace("+00:00", "Z"),
        }

        def _save() -> None:
            with session_scope(self.session_factory) as s:
                s.add(
                    AgentEvent(
                        run_id=run_id,
                        seq=seq,
                        type=type,
                        agent=agent,
                        message=message,
                        data=data,
                        created_at=now,
                    )
                )

        await asyncio.to_thread(_save)
        self.broadcast(run_id, event)
        if type == "run_end":
            self._seq.pop(run_id, None)

    # --- 執行 ---

    async def _work_loop(self) -> None:
        while True:
            run_id = await self._queue.get()
            task = asyncio.create_task(self._execute(run_id), name=f"sagent-run-{run_id}")
            self._tasks[run_id] = task
            try:
                await task
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise  # worker 自己被取消（伺服器關閉）
                # 工作還沒開始執行就被取消，_execute 沒有機會處理，這裡補上
                await self._mark_cancelled(run_id)
            except Exception:  # _execute 已處理錯誤，這裡只是保險
                log.exception("AI 工作 %s 發生未預期錯誤", run_id)
            finally:
                self._tasks.pop(run_id, None)

    def _load_input(
        self, run_id: int
    ) -> tuple[RunInput, str, dict[str, Any], dict[str, Any] | None, dict[str, Any]] | str | None:
        """讀取執行需要的資料並標成執行中。None：不用執行；字串：失敗原因。

        使用者確認過大綱的工作，另外回傳存好的大綱和規劃階段的用量。
        """
        with session_scope(self.session_factory) as s:
            run = s.get(AgentRun, run_id)
            if run is None or run.status != "queued":
                return None
            spec = s.get(Spec, run.spec_id)
            project = s.get(Project, run.project_id)
            if spec is None or project is None:
                run.status = "failed"
                run.error = "找不到 Spec 或專案"
                run.finished_at = utcnow()
                return run.error
            phase = s.get(Phase, spec.phase_id)
            head = spec_service.get_head(s, spec)
            run.status = "running"
            run.started_at = utcnow()
            run.base_version_no = spec.head_version_no
            opts = dict(run.options or {})
            template = get_template(self.templates_provider(), run.doc_type or spec.doc_type)
            inp = RunInput(
                mode="revise" if run.mode == "revise" else "draft",
                project_name=project.name,
                project_description=project.description,
                project_terms=parse_terms(project.glossary or [], limit=GLOSSARY_LIMIT),
                phase_name=phase.name if phase else "",
                spec_title=spec.title,
                template=template,
                requirement=run.requirement,
                existing_content=head.content if head else "",
                options=PipelineOptions(
                    uml_syntax=opts.get("uml_syntax", "auto"),
                    plantuml_available=self.plantuml_available(),
                    max_revisions=int(opts.get("max_revisions", 1)),
                    temperature=float(opts.get("temperature", 0.4)),
                    diagrams=bool(opts.get("diagrams", True)),
                    max_diagrams=int(opts.get("max_diagrams", 4)),
                ),
            )
            answers = [a for a in opts.get("answers") or [] if str(a.get("answer", "")).strip()]
            if answers:
                inp.requirement += "\n\n## 待確認問題的回答\n" + "\n".join(
                    f"- 問：{a['question']}\n  答：{a['answer'].strip()}" for a in answers
                )
            saved_plan = run.plan if opts.get("plan_confirmed") and run.plan else None
            return inp, run.model, opts, saved_plan, dict(run.stats or {}) if saved_plan else {}

    async def _in_thread(self, fn, *args):
        """在執行緒跑資料庫工作。被取消時先等執行緒裡的交易結束再往外丟，避免狀態事後被它蓋掉。"""
        future = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            try:
                await future
            except Exception:
                pass
            raise

    async def _execute(self, run_id: int) -> None:
        emitter = RunEmitter(self, run_id)
        llm: LLMClient | None = None
        pipeline: SpecPipeline | None = None
        try:
            loaded = await self._in_thread(self._load_input, run_id)
            if loaded is None:
                return
            if isinstance(loaded, str):
                await emitter.emit(
                    "run_end", "system", f"失敗：{loaded}", {"status": "failed", "error": loaded}
                )
                return
            inp, model, opts, saved_plan, saved_stats = loaded
            verb = "繼續執行" if saved_plan else "開始執行"
            await emitter.emit("run_status", "system", f"{verb}（模型：{model}）", {"status": "running"})
            if inp.mode == "revise" and not inp.existing_content.strip():
                inp.mode = "draft"
                await emitter.emit("log", "system", "文件目前沒有內容，改用撰寫模式", {"level": "info"})

            llm = self.llm_factory(model, opts)
            pipeline = SpecPipeline(llm, emitter, {k: AgentStats(**v) for k, v in saved_stats.items()})
            if saved_plan is None and opts.get("plan_first") and inp.mode == "draft":
                plan = await pipeline.plan_only(inp)
                await self._in_thread(self._save_planned, run_id, plan, pipeline)
                await emitter.emit(
                    "run_paused",
                    "system",
                    "大綱完成，請回答待確認問題、確認大綱後繼續撰寫",
                    {"status": "planned"},
                )
                return
            result = await pipeline.run(inp, plan=Plan.from_dict(saved_plan) if saved_plan else None)
            stats = {k: asdict(v) for k, v in pipeline.stats.items()}

            def _finish() -> tuple[int | None, bool, int]:
                with session_scope(self.session_factory) as s:
                    run = s.get(AgentRun, run_id)
                    spec = s.get(Spec, run.spec_id)
                    edited_during_run = spec.head_version_no != run.base_version_no
                    label = "AI 修訂" if inp.mode == "revise" else "AI 撰寫"
                    version, created = spec_service.add_version(
                        s,
                        spec,
                        result.markdown,
                        message=f"{label}：{result.summary}"[:500],
                        source="agent",
                        run_id=run_id,
                    )
                    run.status = "succeeded"
                    run.finished_at = utcnow()
                    run.plan = result.plan.to_dict()
                    run.review = {
                        "final": result.review.to_dict() if result.review else None,
                        "rounds": [r.to_dict() for r in result.reviews],
                    }
                    run.stats = stats
                    run.result_version_no = version.version_no if created else None
                    return run.result_version_no, edited_during_run, version.version_no

            version_no, edited, head_no = await self._in_thread(_finish)

            async def _announce() -> None:
                if edited:
                    await emitter.emit(
                        "log",
                        "system",
                        "執行期間文件有被修改，AI 結果存成最新版本，請用版本比較確認差異",
                        {"level": "warn"},
                    )
                if version_no is None:
                    message = f"完成，內容跟第 {head_no} 版相同，沒有產生新版本"
                else:
                    message = f"完成，已存成第 {version_no} 版"
                await emitter.emit(
                    "run_end",
                    "system",
                    message,
                    {"status": "succeeded", "version_no": version_no, "stats": stats},
                )

            # 結果已經存好了，這時候就算被取消也要把完成的消息送出去
            await asyncio.shield(_announce())
        except asyncio.CancelledError:
            await self._mark_cancelled(run_id, pipeline=pipeline)
        except Exception as exc:
            log.exception("AI 工作 %s 失敗", run_id)
            message = str(exc) or exc.__class__.__name__
            await asyncio.to_thread(self._mark, run_id, "failed", message, pipeline)
            await emitter.emit(
                "run_end", "system", f"失敗：{message}", {"status": "failed", "error": message}
            )
        finally:
            if llm is not None:
                await llm.close()

    def _save_planned(self, run_id: int, plan: Plan, pipeline: SpecPipeline) -> None:
        with session_scope(self.session_factory) as s:
            run = s.get(AgentRun, run_id)
            run.status = "planned"
            run.plan = plan.to_dict()
            run.stats = {k: asdict(v) for k, v in pipeline.stats.items()}

    def _mark(self, run_id: int, status: str, error: str, pipeline: SpecPipeline | None) -> None:
        with session_scope(self.session_factory) as s:
            run = s.get(AgentRun, run_id)
            if run is None:
                return
            run.status = status
            run.error = error
            run.finished_at = utcnow()
            if pipeline is not None:
                run.stats = {k: asdict(v) for k, v in pipeline.stats.items()}
