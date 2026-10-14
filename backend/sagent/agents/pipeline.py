"""串起三個 agent：Planner 規劃 → Writer 撰寫 → Reviewer 審查 →（需要時）Writer 修訂 → 再審查。"""

from __future__ import annotations

from ..services.markdown_doc import MdDoc, MdSection, render_markdown
from .base import Emitter
from .llm import LLMClient
from .planner import Planner
from .reviewer import Reviewer
from .textutil import bullet_text, is_bullet
from .types import OPEN_QUESTIONS_HEADING, AgentStats, Draft, PipelineResult, Plan, Review, RunInput
from .writer import Writer


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
    new_items: list[str] = []
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
    def __init__(self, llm: LLMClient, emitter: Emitter):
        self.emitter = emitter
        self.stats: dict[str, AgentStats] = {}
        self.planner = Planner(llm, emitter, self.stats)
        self.writer = Writer(llm, emitter, self.stats)
        self.reviewer = Reviewer(llm, emitter, self.stats)

    async def run(self, inp: RunInput) -> PipelineResult:
        emit = self.emitter.emit
        await emit("phase", "planner", "Planner：規劃文件", {"phase": "plan"})
        plan = await self.planner.plan(inp)

        await emit("phase", "writer", "Writer：撰寫內容", {"phase": "write"})
        draft = await self.writer.write(inp, plan)

        reviews: list[Review] = []
        revisions = 0
        focus: set[str] | None = None
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
                break
            if revisions >= inp.options.max_revisions:
                await emit("log", "reviewer", "已達修訂次數上限，剩下的問題保留在審查報告", {"level": "info"})
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
        )
