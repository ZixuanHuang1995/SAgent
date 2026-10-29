"""Writer：依大綱逐節撰寫，再逐張畫圖；審查後依意見修訂。

順序是「先寫完所有章節文字，再畫所有的圖」。
同類型的呼叫連在一起，提示詞前段相同，Ollama 可以重用快取，CPU 上快很多。
"""

from __future__ import annotations

from ..services.diagrams import extract_code, extract_diagrams, fence, mermaid_kind, normalize_diagram
from .base import AgentBase
from .diagram_kinds import KINDS
from .prompts import (
    DIAGRAM_SYSTEM,
    diagram_fix_message,
    diagram_messages,
    revise_section_messages,
    section_messages,
    update_section_messages,
)
from .textutil import (
    clean_section_body,
    diagrams_to_markers,
    extract_item_ids,
    markers_to_diagrams,
    select_known_items,
    text_length,
)
from .types import DiagramPlan, Draft, Issue, Plan, Review, RunInput, SectionDraft, SectionPlan

_MERMAID_TO_KIND = {
    "flowchart": "flowchart",
    "graph": "flowchart",
    "sequenceDiagram": "sequence",
    "classDiagram": "class",
    "stateDiagram": "state",
    "stateDiagram-v2": "state",
    "erDiagram": "er",
    "gantt": "gantt",
}

MAX_REVISE_SECTIONS = 4


def append_diagram(body: str, caption: str, lang: str, code: str) -> str:
    return f"{body.rstrip()}\n\n**{caption}**\n\n{fence(lang, code)}"


def _other_items(draft: Draft, section_id: str) -> list[str]:
    """其他章節的編號項目，修改一節時給模型對照。"""
    items: list[str] = []
    for sec in draft.sections:
        if sec.id != section_id:
            items.extend(extract_item_ids(sec.body))
    return select_known_items(items)


class Writer(AgentBase):
    role = "writer"

    async def section_update(self, sec: SectionDraft) -> None:
        """把章節最新內容送到畫面，讓使用者在執行中就能預覽。"""
        await self.emitter.emit(
            "section",
            self.role,
            sec.heading,
            {"section_id": sec.id, "heading": sec.heading, "body": sec.body},
        )

    async def write(self, inp: RunInput, plan: Plan) -> Draft:
        drafts: dict[str, SectionDraft] = {}
        known_items: list[str] = []
        for number, sec in enumerate(plan.sections, start=1):
            if sec.action == "keep":
                draft = SectionDraft(sec.id, sec.heading, sec.source_body.strip())
                known_items.extend(extract_item_ids(draft.body))
            elif sec.action == "update":
                draft = SectionDraft(sec.id, sec.heading, await self._update_section(inp, plan, sec))
                known_items.extend(extract_item_ids(draft.body))
            else:
                body = await self._write_section(inp, plan, sec, number, select_known_items(known_items))
                draft = SectionDraft(sec.id, sec.heading, body)
                known_items.extend(extract_item_ids(body))
            drafts[sec.id] = draft
            await self.section_update(draft)

        for number, sec in enumerate(plan.sections, start=1):
            for k, dp in enumerate(sec.diagrams, start=1):
                draft = drafts[sec.id]
                code, lang = await self._draw(inp, sec, draft.body, dp, f"{number}-{k}")
                draft.body = append_diagram(draft.body, f"圖 {number}-{k}：{dp.title}", lang, code)
                await self.section_update(draft)
        return Draft([drafts[s.id] for s in plan.sections])

    async def _write_section(
        self, inp: RunInput, plan: Plan, sec: SectionPlan, number: int, known_items: list[str]
    ) -> str:
        step = f"section:{sec.id}"
        async with self.step(step, f"撰寫第 {number} 節：{sec.heading}") as info:
            result = await self.chat(
                step,
                section_messages(inp, plan, sec, number, known_items),
                temperature=inp.options.temperature,
                max_tokens=4000,
            )
            if result.truncated:
                await self.log(f"第 {number} 節超過長度上限，內容可能不完整", "warn", section_id=sec.id)
            body = clean_section_body(result.text, sec.heading, keep_diagrams=False)
            if not body:
                await self.log(f"第 {number} 節沒有產生內容", "warn", section_id=sec.id)
                body = "（待補充）"
            info.output = {"section_id": sec.id, "chars": len(body)}
            info.message = f"第 {number} 節完成（{len(body)} 字）"
        return body

    async def _update_section(self, inp: RunInput, plan: Plan, sec: SectionPlan) -> str:
        step = f"section:{sec.id}"
        async with self.step(step, f"修改：{sec.heading}") as info:
            result = await self.chat(
                step,
                update_section_messages(inp, plan, sec),
                temperature=min(inp.options.temperature, 0.3),
                max_tokens=4000,
            )
            body = clean_section_body(result.text, sec.heading, keep_diagrams=True)
            old_len = text_length(sec.source_body)
            if not body or (result.truncated and text_length(body) < old_len * 0.5):
                await self.log(f"「{sec.heading}」修改結果不完整，保留原內容", "warn", section_id=sec.id)
                body = sec.source_body.strip()
            info.output = {"section_id": sec.id, "chars": len(body)}
            info.message = f"「{sec.heading}」修改完成"
        return body

    async def _draw(
        self, inp: RunInput, sec: SectionPlan, section_text: str, dp: DiagramPlan, no: str
    ) -> tuple[str, str]:
        lang = dp.syntax
        step = f"diagram:{sec.id}:{no}"
        async with self.step(step, f"繪製圖 {no}：{dp.title}") as info:
            messages = diagram_messages(
                inp, sec.heading, section_text, dp.kind, dp.title, dp.description, lang
            )
            result = await self.chat(step, messages, temperature=0.2, max_tokens=1500)
            code, errors = normalize_diagram(lang, extract_code(result.text, lang))
            if errors:
                await self.log(
                    f"圖 {no} 語法有問題，請模型修正一次：{'；'.join(errors[:3])}", "warn", step=step
                )
                retry = [
                    *messages,
                    {"role": "assistant", "content": fence(lang, code)},
                    diagram_fix_message(errors, lang),
                ]
                result = await self.chat(step, retry, temperature=0.1, max_tokens=1500)
                code2, errors2 = normalize_diagram(lang, extract_code(result.text, lang))
                if len(errors2) <= len(errors):
                    code, errors = code2, errors2
            info.output = {
                "section_id": sec.id,
                "title": dp.title,
                "kind": dp.kind,
                "lang": lang,
                "code": code,
                "errors": errors,
            }
            info.message = f"圖 {no} 完成" if not errors else f"圖 {no} 仍有語法問題，交給審查"
        return code, lang

    # --- 依審查意見修訂 ---

    async def revise(
        self, inp: RunInput, plan: Plan, draft: Draft, review: Review, round_no: int
    ) -> set[str]:
        """修改有 high/medium 問題的章節。回傳實際被修改的章節編號。"""
        targets: dict[str, list[Issue]] = {}
        for issue in review.issues:
            if issue.severity not in ("high", "medium") or not issue.section_id:
                continue
            sec_plan = plan.section(issue.section_id)
            if sec_plan is None or sec_plan.action == "keep":
                continue  # 沒有要改的舊章節不動
            targets.setdefault(issue.section_id, []).append(issue)

        changed: set[str] = set()
        for count, (section_id, issues) in enumerate(targets.items()):
            if count >= MAX_REVISE_SECTIONS:
                await self.log(f"這一輪最多修改 {MAX_REVISE_SECTIONS} 節，其餘問題保留在審查報告", "info")
                break
            sec_plan = plan.section(section_id)
            sec = draft.section(section_id)
            assert sec_plan is not None and sec is not None
            diagram_issues = [i for i in issues if i.diagram_index is not None]
            text_issues = [i for i in issues if i.diagram_index is None]
            for issue in diagram_issues:
                if await self._fix_diagram(inp, sec, issue, round_no):
                    changed.add(section_id)
            if text_issues and await self._revise_text(
                inp, plan, sec_plan, sec, text_issues, round_no, _other_items(draft, section_id)
            ):
                changed.add(section_id)
        return changed

    async def _revise_text(
        self,
        inp: RunInput,
        plan: Plan,
        sec_plan: SectionPlan,
        sec: SectionDraft,
        issues: list[Issue],
        round_no: int,
        known_items: list[str],
    ) -> bool:
        step = f"revise:{sec.id}:{round_no}"
        async with self.step(step, f"依審查意見修改：{sec.heading}") as info:
            marked, originals = diagrams_to_markers(sec.body)
            result = await self.chat(
                step,
                revise_section_messages(inp, plan, sec_plan, marked, issues, known_items),
                temperature=min(inp.options.temperature, 0.3),
                max_tokens=3000,
            )
            new_text = clean_section_body(result.text, sec.heading, keep_diagrams=False)
            # 模型偷懶只回一小段時不採用，避免內容變少
            if not new_text or text_length(new_text) < text_length(marked) * 0.4:
                await self.log(f"「{sec.heading}」修改後內容明顯變少，保留原內容", "warn", section_id=sec.id)
                info.message = f"「{sec.heading}」保留原內容"
                return False
            sec.body = markers_to_diagrams(new_text, originals)
            info.output = {"section_id": sec.id, "issues": len(issues)}
            info.message = f"「{sec.heading}」已修改"
        await self.section_update(sec)
        return True

    async def _fix_diagram(self, inp: RunInput, sec: SectionDraft, issue: Issue, round_no: int) -> bool:
        blocks = extract_diagrams(sec.body)
        idx = issue.diagram_index or 0
        if idx >= len(blocks):
            return False
        block = blocks[idx]
        step = f"fixdiagram:{sec.id}:{idx + 1}:{round_no}"
        async with self.step(step, f"修正「{sec.heading}」的第 {idx + 1} 張圖") as info:
            kind = _MERMAID_TO_KIND.get(mermaid_kind(block.code), "") if block.lang == "mermaid" else ""
            guide = example = ""
            if kind:
                guide, example = KINDS[kind].mermaid_guide, KINDS[kind].mermaid_example
            content = f"## 有語法錯誤的圖\n{fence(block.lang, block.code)}\n"
            if guide:
                content += f"\n## 語法規則\n{guide}\n\n## 範例\n{fence(block.lang, example)}\n"
            messages = [
                {"role": "system", "content": DIAGRAM_SYSTEM},
                {
                    "role": "user",
                    "content": content + "\n" + diagram_fix_message([issue.problem], block.lang)["content"],
                },
            ]
            result = await self.chat(step, messages, temperature=0.1, max_tokens=1500)
            code, errors = normalize_diagram(block.lang, extract_code(result.text, block.lang))
            if errors:
                info.message = f"第 {idx + 1} 張圖仍有語法問題"
                info.output = {"section_id": sec.id, "errors": errors}
                return False
            lines = sec.body.splitlines()
            closing = lines[block.end] if block.closed else block.fence
            new_lines = lines[: block.start + 1] + code.splitlines() + [closing] + lines[block.end + 1 :]
            sec.body = "\n".join(new_lines)
            info.output = {"section_id": sec.id, "code": code}
            info.message = f"第 {idx + 1} 張圖已修正"
        await self.section_update(sec)
        return True
