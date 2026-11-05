"""AI 工作的背景執行器。

- 工作排隊，一次只跑一個（CPU 推論同時跑多個只會更慢）
- 執行過程的事件存進資料庫，並即時推給正在看的畫面（SSE）
- 逐字串流不存資料庫，只即時推送，每 0.15 秒合併送一次
- 伺服器重啟時：排隊中的工作重新排隊；執行到一半的工作有檢查點（大綱和寫好的章節）就重新排隊、
  從中斷的地方繼續，沒有才標成失敗。審查和修訂不存檢查點，續跑時從第一次審查重來
- 伺服器正常關閉時，執行中的工作不標成取消，留給下次啟動續跑
- 先確認大綱的工作：規劃完存成 planned 停下來（不占佇列），使用者確認後重新排隊，沿用存好的大綱繼續
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..db import session_scope, utcnow
from ..models import AgentEvent, AgentRun, Phase, Project, Spec
from ..services import specs as spec_service
from ..services.power import KeepAwake
from .doc_templates import DocTemplate, get_template
from .llm import LLMClient
from .pipeline import SpecPipeline
from .planner import GLOSSARY_LIMIT, parse_terms
from .types import AgentStats, PipelineOptions, Plan, RunInput, WriteProgress
from .versioning import prompt_version

log = logging.getLogger("sagent.runner")

TOKEN_FLUSH_SECONDS = 0.15
INTERRUPTED = "伺服器重新啟動，工作被中斷"


@dataclass
class _Loaded:
    """開始執行時從資料庫讀出來的東西。"""

    inp: RunInput
    model: str
    opts: dict[str, Any]
    saved_plan: dict[str, Any] | None  # 使用者確認過的大綱
    saved_stats: dict[str, Any]
    checkpoint: dict[str, Any] | None  # 伺服器重啟前存的檢查點
    prior_wall: float = 0.0  # 續跑、確認大綱後繼續時，之前已經用掉的秒數（C-06）


class WallTimeExceeded(Exception):
    """超過整個工作的時間上限（設定的 max_run_minutes，C-06）。"""


def can_resume(checkpoint: dict[str, Any] | None) -> bool:
    return bool(checkpoint) and isinstance(checkpoint.get("plan"), dict)


def requirement_with_answers(requirement: str, options: dict[str, Any]) -> str:
    """先確認大綱時使用者的回答接在需求後面，Writer 和 Reviewer 看到的需求就是這個。

    重新審查舊工作（scripts/review_consistency.py）也要用一樣的需求，不然回答過的內容會被當成「需求沒提到」。
    """
    answers = [a for a in options.get("answers") or [] if str(a.get("answer", "")).strip()]
    if not answers:
        return requirement
    return (
        requirement
        + "\n\n## 待確認問題的回答\n"
        + "\n".join(f"- 問：{a['question']}\n  答：{a['answer'].strip()}" for a in answers)
    )


async def _environment(llm: LLMClient) -> dict[str, Any]:
    """模型的執行環境（Ollama 版本、digest、量化）。沒有這個方法的模型（測試用的）回 {}。"""
    get = getattr(llm, "environment", None)
    if get is None:
        return {}
    try:
        return dict(await get())
    except Exception:  # noqa: BLE001  記錄環境失敗不能讓工作失敗
        log.warning("讀取模型環境失敗", exc_info=True)
        return {}


def _optional_int(value: Any) -> int | None:
    """沒有值表示不限制（例如圖的張數）。舊的 AI 工作存的是數字，照樣用。"""
    return None if value is None else int(value)


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
        keep_awake: KeepAwake | None = None,
    ):
        self.session_factory = session_factory
        # AI 工作跑的時候電腦不能進入待命，不然程式會被暫停到有人喚醒（services/power.py）
        self.keep_awake = keep_awake or KeepAwake(enabled=False)
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
        self._stopping = False
        # 執行中的工作：run_id → (之前已經用掉的秒數, 這一段開始的時間)，算 wall_seconds 用（C-06）
        self._wall: dict[int, tuple[float, float]] = {}

    # --- 生命週期 ---

    async def start(self) -> None:
        self._stopping = False
        resumed: list[int] = []
        with session_scope(self.session_factory) as s:
            for run in s.scalars(select(AgentRun).where(AgentRun.status == "running").order_by(AgentRun.id)):
                if can_resume(run.checkpoint):
                    run.status = "queued"  # 有檢查點：先跑，從中斷的地方繼續
                    resumed.append(run.id)
                else:
                    run.status = "failed"
                    run.error = INTERRUPTED
                    run.stop_reason = "failed"
                    run.finished_at = utcnow()
            queued = list(
                s.scalars(select(AgentRun.id).where(AgentRun.status == "queued").order_by(AgentRun.id))
            )
        for run_id in resumed + [r for r in queued if r not in resumed]:
            self._queue.put_nowait(run_id)
        self._worker = asyncio.create_task(self._work_loop(), name="sagent-run-worker")

    async def stop(self) -> None:
        # 伺服器關閉：執行中的工作維持 running，下次啟動時有檢查點就續跑
        self._stopping = True
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
                run.stop_reason = "cancelled"
                self._record_wall(run)
                run.checkpoint = None
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
                if not task.cancelled() or self._stopping:
                    raise  # worker 自己被取消（伺服器關閉）
                # 工作還沒開始執行就被取消，_execute 沒有機會處理，這裡補上
                await self._mark_cancelled(run_id)
            except Exception:  # _execute 已處理錯誤，這裡只是保險
                log.exception("AI 工作 %s 發生未預期錯誤", run_id)
            finally:
                self._tasks.pop(run_id, None)

    def _load_input(self, run_id: int) -> _Loaded | str | None:
        """讀取執行需要的資料並標成執行中。None：不用執行；字串：失敗原因。

        使用者確認過大綱的工作，另外回傳存好的大綱和規劃階段的用量；續跑的工作回傳檢查點。
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
            checkpoint = dict(run.checkpoint) if can_resume(run.checkpoint) else None
            run.status = "running"
            if checkpoint is None:
                run.started_at = utcnow()
                run.base_version_no = spec.head_version_no
            # 續跑：開始時間和根據的版本照第一次開始時的，才看得出執行期間有沒有人改過文件
            opts = dict(run.options or {})
            template = get_template(self.templates_provider(), run.doc_type or spec.doc_type)
            # 記下用的是哪一版提示詞、範本和程式（D41）。續跑時程式可能改過，另外記一份
            version = prompt_version(template)
            if checkpoint is None or "prompt_version" not in opts:
                opts["prompt_version"] = version
            elif opts["prompt_version"] != version:
                opts["prompt_version_resumed"] = version
            run.options = opts
            inp = RunInput(
                mode="revise" if run.mode == "revise" else "draft",
                project_name=project.name,
                project_description=project.description,
                project_terms=parse_terms(project.glossary or [], limit=GLOSSARY_LIMIT),
                phase_name=phase.name if phase else "",
                spec_title=spec.title,
                template=template,
                requirement=requirement_with_answers(run.requirement, opts),
                existing_content=head.content if head else "",
                options=PipelineOptions(
                    uml_syntax=opts.get("uml_syntax", "auto"),
                    plantuml_available=self.plantuml_available(),
                    max_revisions=int(opts.get("max_revisions", 2)),
                    temperature=float(opts.get("temperature", 0.4)),
                    diagrams=bool(opts.get("diagrams", True)),
                    max_diagrams=_optional_int(opts.get("max_diagrams")),
                    store_prompts=bool(opts.get("store_prompts")),
                ),
            )
            saved_plan = run.plan if opts.get("plan_confirmed") and run.plan else None
            keep_stats = saved_plan is not None or checkpoint is not None
            return _Loaded(
                inp,
                run.model,
                opts,
                saved_plan,
                dict(run.stats or {}) if keep_stats else {},
                checkpoint,
                prior_wall=float(run.wall_seconds or 0) if keep_stats else 0.0,
            )

    def _interrupted_steps(self, run_id: int) -> list[tuple[str, str, str]]:
        """開始了、沒有結束的步驟（伺服器重啟時中斷的）：[(agent, step, 標籤)]。"""
        with session_scope(self.session_factory) as s:
            events = s.scalars(
                select(AgentEvent)
                .where(AgentEvent.run_id == run_id, AgentEvent.type.in_(("step_start", "step_end")))
                .order_by(AgentEvent.seq)
            ).all()
            open_steps: dict[str, tuple[str, str, str]] = {}
            for ev in events:
                step = str((ev.data or {}).get("step", ""))
                if ev.type == "step_start":
                    open_steps[step] = (ev.agent, step, ev.message)
                else:
                    open_steps.pop(step, None)
            return list(open_steps.values())

    def _save_checkpoint(
        self, run_id: int, plan: Plan, progress: WriteProgress, pipeline: SpecPipeline
    ) -> None:
        with session_scope(self.session_factory) as s:
            run = s.get(AgentRun, run_id)
            if run is None or run.status != "running":
                return
            run.checkpoint = {"plan": plan.to_state(), "progress": progress.to_dict()}
            run.stats = {k: asdict(v) for k, v in pipeline.stats.items()}
            self._record_wall(run)

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
        reviewer_llm: LLMClient | None = None
        pipeline: SpecPipeline | None = None
        awake = False
        try:
            loaded = await self._in_thread(self._load_input, run_id)
            if loaded is None:
                return
            if isinstance(loaded, str):
                await emitter.emit(
                    "run_end", "system", f"失敗：{loaded}", {"status": "failed", "error": loaded}
                )
                return
            self.keep_awake.hold()
            awake = True
            self._wall[run_id] = (loaded.prior_wall, time.time())
            inp, model, opts, saved_plan = loaded.inp, loaded.model, loaded.opts, loaded.saved_plan
            limit_s = float(opts.get("max_run_minutes") or 0) * 60

            async def limited(coro):
                """整個工作的時間上限（C-06）：從第一次開始算，續跑時扣掉已經用掉的時間。"""
                if limit_s <= 0:
                    return await coro
                message = f"超過整個工作的時間上限（{limit_s / 60:g} 分鐘）"
                remaining = limit_s - (self._wall_seconds(run_id) or 0)
                if remaining <= 0:
                    coro.close()
                    raise WallTimeExceeded(message)
                try:
                    return await asyncio.wait_for(coro, timeout=remaining)
                except TimeoutError as exc:
                    if (self._wall_seconds(run_id) or 0) < limit_s - 1:
                        raise  # 不是時間上限，是裡面自己逾時
                    raise WallTimeExceeded(message) from exc
            checkpoint = loaded.checkpoint
            progress: WriteProgress | None = None
            plan: Plan | None = Plan.from_dict(saved_plan) if saved_plan else None
            if checkpoint is not None:
                plan = Plan.from_state(checkpoint["plan"])
                progress = WriteProgress.from_dict(checkpoint.get("progress"))
                for agent, step, label in await self._in_thread(self._interrupted_steps, run_id):
                    await emitter.emit(
                        "step_end",
                        agent,
                        f"{label}：伺服器重新啟動，中斷",
                        {"step": step, "ok": False, "error": INTERRUPTED},
                    )
                done = sum(1 for s in plan.sections if s.id in progress.sections)
                where = "從第一次審查重來" if progress.done else f"已經寫好 {done} 節，接著寫"
                message = f"伺服器重新啟動後繼續執行（模型：{model}），{where}"
            else:
                message = f"{'繼續執行' if saved_plan else '開始執行'}（模型：{model}）"
            await emitter.emit("run_status", "system", message, {"status": "running"})
            if self.keep_awake.active:
                await emitter.emit(
                    "log",
                    "system",
                    "執行期間電腦不會自動待命，螢幕會保持亮著；闔上螢幕或按電源鍵還是會待命",
                    {"level": "info"},
                )
            if inp.mode == "revise" and not inp.existing_content.strip():
                inp.mode = "draft"
                await emitter.emit("log", "system", "文件目前沒有內容，改用撰寫模式", {"level": "info"})

            llm = self.llm_factory(model, opts)
            # C-03：設定了不同的審查模型時，Reviewer 用另一個模型（工作建立時記在 options）
            review_model = str(opts.get("reviewer_model") or "").strip()
            if review_model and review_model != model:
                reviewer_llm = self.llm_factory(review_model, opts)
            env = await _environment(llm)
            if reviewer_llm is not None and (reviewer_env := await _environment(reviewer_llm)):
                env["reviewer"] = reviewer_env
            await self._in_thread(self._save_environment, run_id, env, checkpoint is not None)
            pipeline = SpecPipeline(
                llm,
                emitter,
                {k: AgentStats(**v) for k, v in loaded.saved_stats.items()},
                reviewer_llm=reviewer_llm,
            )
            if plan is None and opts.get("plan_first") and inp.mode == "draft":
                plan = await limited(pipeline.plan_only(inp))
                await self._in_thread(self._save_planned, run_id, plan, pipeline)
                await emitter.emit(
                    "run_paused",
                    "system",
                    "大綱完成，請回答待確認問題、確認大綱後繼續撰寫",
                    {"status": "planned"},
                )
                return
            assert pipeline is not None
            running = pipeline

            async def save_checkpoint(p: Plan, w: WriteProgress) -> None:
                await self._in_thread(self._save_checkpoint, run_id, p, w, running)

            result = await limited(
                pipeline.run(inp, plan=plan, progress=progress, on_checkpoint=save_checkpoint)
            )
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
                    run.stop_reason = result.stop_reason or None
                    self._record_wall(run)
                    run.plan = result.plan.to_dict()
                    run.review = {
                        "final": result.review.to_dict() if result.review else None,
                        "rounds": [r.to_dict() for r in result.reviews],
                    }
                    run.stats = stats
                    run.checkpoint = None
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
            if self._stopping:
                raise  # 伺服器關閉：維持 running，下次啟動時從檢查點續跑
            await self._mark_cancelled(run_id, pipeline=pipeline)
        except WallTimeExceeded as exc:
            # 檢查點不清掉：寫好的章節還在，之後要分析都找得到
            message = str(exc)
            await asyncio.to_thread(
                self._mark, run_id, "failed", message, pipeline, "walltime", keep_checkpoint=True
            )
            await emitter.emit(
                "run_end", "system", f"失敗：{message}", {"status": "failed", "error": message}
            )
        except Exception as exc:
            log.exception("AI 工作 %s 失敗", run_id)
            message = str(exc) or exc.__class__.__name__
            await asyncio.to_thread(self._mark, run_id, "failed", message, pipeline)
            await emitter.emit(
                "run_end", "system", f"失敗：{message}", {"status": "failed", "error": message}
            )
        finally:
            self._wall.pop(run_id, None)
            if awake:
                self.keep_awake.release()
            if llm is not None:
                await llm.close()
            if reviewer_llm is not None:
                await reviewer_llm.close()

    def _save_environment(self, run_id: int, env: dict[str, Any], resumed: bool) -> None:
        """記下 Ollama 版本、模型 digest 和量化（C-05）。跟 prompt_version 一樣：續跑時變了另外記一份。"""
        with session_scope(self.session_factory) as s:
            run = s.get(AgentRun, run_id)
            if run is None:
                return
            opts = dict(run.options or {})
            if not resumed or "environment" not in opts:
                opts["environment"] = env
            elif opts["environment"] != env:
                opts["environment_resumed"] = env
            run.options = opts

    def _save_planned(self, run_id: int, plan: Plan, pipeline: SpecPipeline) -> None:
        with session_scope(self.session_factory) as s:
            run = s.get(AgentRun, run_id)
            run.status = "planned"
            run.plan = plan.to_dict()
            run.stats = {k: asdict(v) for k, v in pipeline.stats.items()}
            self._record_wall(run)

    def _wall_seconds(self, run_id: int) -> float | None:
        """這個工作到現在實際經過的秒數（含之前的段落）。沒在執行時回 None。"""
        seg = self._wall.get(run_id)
        if seg is None:
            return None
        prior, started = seg
        return round(prior + max(time.time() - started, 0), 1)

    def _record_wall(self, run: AgentRun) -> None:
        wall = self._wall_seconds(run.id)
        if wall is not None:
            run.wall_seconds = wall

    def _mark(
        self,
        run_id: int,
        status: str,
        error: str,
        pipeline: SpecPipeline | None,
        stop_reason: str = "failed",
        *,
        keep_checkpoint: bool = False,
    ) -> None:
        with session_scope(self.session_factory) as s:
            run = s.get(AgentRun, run_id)
            if run is None:
                return
            run.status = status
            run.error = error
            run.finished_at = utcnow()
            run.stop_reason = stop_reason
            self._record_wall(run)
            if not keep_checkpoint:
                run.checkpoint = None
            if pipeline is not None:
                run.stats = {k: asdict(v) for k, v in pipeline.stats.items()}
