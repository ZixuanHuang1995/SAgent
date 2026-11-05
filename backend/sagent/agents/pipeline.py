"""串起三個 agent：Planner 規劃 → Writer 撰寫 → Reviewer 審查 →（需要時）Writer 修訂 → 再審查。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..services.markdown_doc import MdDoc, MdSection, render_markdown
from .base import Emitter, TokenEstimate
from .llm import LLMClient
from .planner import Planner
from .reviewer import Reviewer
from .textutil import bullet_text, is_bullet
from .types import (
    OPEN_QUESTIONS_HEADING,
    AgentStats,
    Draft,
    PipelineResult,
    Plan,
    Review,
    RunInput,
    WriteProgress,
)
from .writer import Writer

CheckpointCallback = Callable[[Plan, WriteProgress], Awaitable[None]]


def assemble(inp: RunInput, plan: Plan, draft: Draft) -> str:
    if inp.mode == "revise":
        preamble = plan.preamble  # 修訂時保留原文件開頭，原本沒有就不加
    else:
        info = f"> 文件類型：{inp.template.name}｜專案：{inp.project_name}｜階段：{inp.phase_name}"
        preamble = f"# {inp.spec_title}\n\n{info}"
        if plan.summary.strip():
            preamble += f"\n\n{plan.summary.strip()}"
    sections = [MdSection(f"{i}. {sec.heading}", sec.body) for i, sec in enumerate(draft.sections, start=1)]

    # 待確認事項：原本的內容整段保留（可能有表格或說明），只在後面加上新的問題
    body = plan.existing_questions_body.rstrip()
    new_items: list[str] = [f"- [x] {bullet_text(q)}（已確認：{a}）" for q, a in plan.answered]
    for q in plan.open_questions:
        text = bullet_text(q)
        if text and text not in body and not any(text in item for item in new_items):
            new_items.append(f"- [ ] {text}")
    if new_items:
        # 原本最後一行是條列就直接接著寫，否則空一行
        joiner = "\n" if body and is_bullet(body.splitlines()[-1]) else "\n\n"
        body = (body + joiner if body else "") + "\n".join(new_items)
    if body.strip():
        sections.append(MdSection(f"{len(sections) + 1}. {OPEN_QUESTIONS_HEADING}", body))
    return render_markdown(MdDoc(preamble=preamble, sections=sections))


class SpecPipeline:
    def __init__(
        self,
        llm: LLMClient,
        emitter: Emitter,
        stats: dict[str, AgentStats] | None = None,
        reviewer_llm: LLMClient | None = None,
    ):
        """stats：先確認大綱的工作繼續撰寫時，帶入規劃階段的用量，最後的統計才完整。

        reviewer_llm：審查用的模型（C-03）。沒給就跟 Planner、Writer 用同一個。
        同一個模型審查自己寫的文件，容易偏好自己的輸出（第 15 堂：self-enhancement bias）。
        """
        self.emitter = emitter
        self.stats: dict[str, AgentStats] = stats if stats is not None else {}
        self.planner = Planner(llm, emitter, self.stats)
        self.writer = Writer(llm, emitter, self.stats)
        self.reviewer = Reviewer(reviewer_llm or llm, emitter, self.stats)
        # Writer 實際用掉的 token 數拿來估計審查的輸入有多長（C-14）
        estimate = TokenEstimate()
        for agent in (self.planner, self.writer, self.reviewer):
            agent.token_estimate = estimate

    @staticmethod
    def _apply_template_limits(inp: RunInput) -> None:
        if not inp.template.allow_diagrams:
            inp.options.diagrams = False  # 這種文件類型不畫圖（例如 UI/UX 設計建議）
        # 範本有設 max_diagrams 時（只有自訂範本會設）照範本，只會變少
        limit = inp.template.max_diagrams
        if limit is not None:
            current = inp.options.max_diagrams
            inp.options.max_diagrams = limit if current is None else min(current, limit)

    def _prepare(self, inp: RunInput) -> None:
        self._apply_template_limits(inp)
        for agent in (self.planner, self.writer, self.reviewer):
            agent.store_prompts = inp.options.store_prompts

    async def plan_only(self, inp: RunInput) -> Plan:
        """先確認大綱：只跑 Planner，大綱和待確認問題交給使用者確認後，再用 run(plan=…) 繼續。"""
        self._prepare(inp)
        await self.emitter.emit("phase", "planner", "Planner：規劃文件", {"phase": "plan"})
        return await self.planner.plan(inp)

    async def run(
        self,
        inp: RunInput,
        plan: Plan | None = None,
        progress: WriteProgress | None = None,
        on_checkpoint: CheckpointCallback | None = None,
    ) -> PipelineResult:
        """plan 有值時（使用者已經確認過的大綱，或續跑時存好的大綱）跳過 Planner。

        progress：續跑時上次寫到哪裡。on_checkpoint：規劃完、每寫完一節或一張圖時呼叫，讓執行器存檢查點。
        審查和修訂不存檢查點：中斷後從第一次審查重來（大部分的時間花在撰寫）。
        """
        self._prepare(inp)
        emit = self.emitter.emit
        if plan is None:
            await emit("phase", "planner", "Planner：規劃文件", {"phase": "plan"})
            plan = await self.planner.plan(inp)
        if on_checkpoint is not None and progress is None:
            await on_checkpoint(plan, WriteProgress())  # 有大綱就能續跑，不用重新規劃

        async def on_progress(p: WriteProgress) -> None:
            if on_checkpoint is not None:
                await on_checkpoint(plan, p)

        await emit("phase", "writer", "Writer：撰寫內容", {"phase": "write"})
        draft = await self.writer.write(inp, plan, progress, on_progress if on_checkpoint else None)

        reviews: list[Review] = []
        revisions = 0
        focus: set[str] | None = None
        stop_reason = ""
        while True:
            round_no = len(reviews) + 1
            await emit(
                "phase", "reviewer", f"Reviewer：第 {round_no} 次審查", {"phase": "review", "round": round_no}
            )
            review = await self.reviewer.review(
                inp, plan, draft, round_no, focus=focus, previous=reviews[-1] if reviews else None
            )
            reviews.append(review)
            if not review.needs_revision:
                stop_reason = "passed"
                break
            if revisions >= inp.options.max_revisions:
                stop_reason = "revision_limit"
                await emit("log", "reviewer", "已達修訂次數上限，剩下的問題保留在審查報告", {"level": "info"})
                break
            if len(reviews) >= 2 and review.error_count >= reviews[-2].error_count:
                stop_reason = "no_improvement"
                # 改了一輪錯誤卻沒變少，再改多半也一樣，CPU 上一輪要十幾分鐘，交給人處理
                await emit(
                    "log",
                    "reviewer",
                    f"修訂後錯誤沒有變少（上一輪 {reviews[-2].error_count} 個，這一輪 {review.error_count} 個），"
                    "停止自動修訂，剩下的問題保留在審查報告",
                    {"level": "info"},
                )
                break
            revisions += 1
            await emit(
                "phase",
                "writer",
                f"Writer：依審查意見修訂（第 {revisions} 輪）",
                {"phase": "revise", "round": revisions},
            )
            focus = await self.writer.revise(inp, plan, draft, review, revisions)
            if not focus:
                stop_reason = "nothing_to_revise"
                await emit("log", "writer", "沒有可以自動修訂的章節，結束修訂", {"level": "info"})
                break

        markdown = assemble(inp, plan, draft)
        return PipelineResult(
            markdown=markdown,
            plan=plan,
            review=reviews[-1] if reviews else None,
            reviews=reviews,
            stats=self.stats,
            summary=plan.summary,
            stop_reason=stop_reason,
        )
