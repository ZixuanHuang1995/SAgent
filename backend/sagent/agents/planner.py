"""Planner：決定文件要有哪些章節、每節寫什麼、哪裡要畫圖。

撰寫模式：依範本和需求規劃新大綱。
修訂模式：讀既有文件，決定哪些章節要改、刪、新增，其他保持不變。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..services.markdown_doc import normalize_heading, parse_markdown, strip_heading_number
from .base import AgentBase
from .diagram_kinds import KINDS, resolve_syntax
from .doc_templates import DocTemplate, ids_from_goal, same_heading
from .prompts import REVISE_PLAN_SCHEMA, TERM_KINDS, plan_schema, planner_messages, revise_planner_messages
from .textutil import clean_list, normalize_sid
from .types import OPEN_QUESTIONS_HEADING, DiagramPlan, PipelineOptions, Plan, RunInput, SectionPlan, Term

_OPEN_Q_KEY = normalize_heading(OPEN_QUESTIONS_HEADING)


def _diagrams(raw_list: Any, opts: PipelineOptions) -> list[DiagramPlan]:
    if not opts.diagrams or not isinstance(raw_list, list):
        return []
    out: list[DiagramPlan] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", "")).strip().lower()
        if kind not in KINDS:
            continue
        out.append(
            DiagramPlan(
                kind=kind,
                title=(str(raw.get("title", "")).strip() or KINDS[kind].name)[:60],
                description=str(raw.get("description", "")).strip()[:300],
                syntax=resolve_syntax(kind, opts.uml_syntax, opts.plantuml_available),
            )
        )
    return out[:2]


def parse_terms(raw_list: Any, limit: int = 12) -> list[Term]:
    """名詞表：名稱重複的只留第一個。也用在使用者先確認大綱時改過的名詞表。"""
    if not isinstance(raw_list, list):
        return []
    terms: list[Term] = []
    seen: set[str] = set()
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()[:30]
        if not name or name in seen:
            continue
        seen.add(name)
        kind = str(raw.get("kind", "")).strip()
        terms.append(
            Term(name, kind if kind in TERM_KINDS else "其他", str(raw.get("note", "")).strip()[:100])
        )
    return terms[:limit]


def _limit_diagrams(sections: list[SectionPlan], opts: PipelineOptions) -> None:
    budget = opts.max_diagrams if opts.diagrams else 0
    for sec in sections:
        keep = sec.diagrams[: max(budget, 0)]
        budget -= len(keep)
        sec.diagrams = keep


def _assign_ids(sections: list[SectionPlan]) -> None:
    for i, sec in enumerate(sections, start=1):
        sec.id = f"S{i}"


def _attach_item_ids(tpl: DocTemplate, sections: list[SectionPlan]) -> None:
    """每一節要用的編號：對應得到範本章節就用範本的 ids，對應不到就看目標裡有沒有寫。"""
    for sec in sections:
        if sec.action == "keep" or sec.ids:
            continue
        t = tpl.section_for(sec.heading)
        sec.ids = t.ids if t is not None else ids_from_goal(sec.goal)


def _fit_template(tpl: DocTemplate, planned: list[SectionPlan]) -> list[SectionPlan]:
    """章節固定的範本：章節名稱、順序和目標照範本，只用 Planner 給的要點和圖。

    先配對名稱完全一樣的，再配對互相包含的；Planner 多出來的章節不用，缺少的章節照樣寫。
    """
    unused = list(planned)
    matched: dict[int, SectionPlan] = {}
    for loose in (False, True):
        for i, t in enumerate(tpl.sections):
            if i in matched:
                continue
            key = normalize_heading(t.heading)
            for p in unused:
                if (same_heading(p.heading, t.heading) if loose else normalize_heading(p.heading) == key):
                    matched[i] = p
                    unused.remove(p)
                    break
    fitted = []
    for i, t in enumerate(tpl.sections):
        p = matched.get(i)
        fitted.append(
            SectionPlan(
                id="",
                heading=t.heading,
                goal=t.goal,
                key_points=p.key_points if p else [],
                diagrams=p.diagrams if p else [],
                ids=t.ids,
            )
        )
    return fitted


def fallback_plan(inp: RunInput) -> Plan:
    """模型給不出可用的大綱時，直接用範本的章節。"""
    opts = inp.options
    sections = [
        SectionPlan(
            id="",
            heading=t.heading,
            goal=t.goal,
            diagrams=[
                DiagramPlan(
                    kind=k,
                    title=KINDS[k].name,
                    description=t.goal,
                    syntax=resolve_syntax(k, opts.uml_syntax, opts.plantuml_available),
                )
                for k in t.diagrams
            ]
            if opts.diagrams
            else [],
            ids=t.ids,
        )
        for t in inp.template.sections
    ]
    _limit_diagrams(sections, opts)
    _assign_ids(sections)
    return Plan(title=inp.spec_title, summary=inp.template.description, sections=sections, fallback=True)


class Planner(AgentBase):
    role = "planner"

    async def plan(self, inp: RunInput) -> Plan:
        if inp.mode == "revise":
            return await self._plan_revise(inp)
        return await self._plan_draft(inp)

    # --- 撰寫模式 ---

    async def _plan_draft(self, inp: RunInput) -> Plan:
        async with self.step("plan", "規劃文件大綱") as info:
            data = await self.chat_json(
                "plan", planner_messages(inp), plan_schema(inp.template), max_tokens=2500
            )
            plan = self.build_plan(inp, data) if data else None
            if plan is None:
                await self.log("模型沒有產生可用的大綱，改用範本章節", "warn")
                plan = fallback_plan(inp)
            info.output = plan.to_dict()
            n_diagrams = sum(len(s.diagrams) for s in plan.sections)
            info.message = f"大綱完成：{len(plan.sections)} 節、{n_diagrams} 張圖"
        return plan

    @staticmethod
    def build_plan(inp: RunInput, data: dict[str, Any]) -> Plan | None:
        opts = inp.options
        tpl = inp.template
        sections: list[SectionPlan] = []
        seen: set[str] = set()
        for raw in data.get("sections") or []:
            if not isinstance(raw, dict):
                continue
            heading = strip_heading_number(str(raw.get("heading", "")).strip())[:80]
            key = normalize_heading(heading)
            if not heading or key in seen or key == _OPEN_Q_KEY:
                continue
            seen.add(key)
            sections.append(
                SectionPlan(
                    id="",
                    heading=heading,
                    goal=str(raw.get("goal", "")).strip()[:300],
                    key_points=clean_list(raw.get("key_points"), limit=8, max_len=200),
                    diagrams=_diagrams(raw.get("diagrams"), opts),
                )
            )
            if not tpl.fixed_sections and len(sections) >= opts.max_sections:
                break
        if len(sections) < 2:
            return None
        if tpl.fixed_sections:
            sections = _fit_template(tpl, sections)
        _attach_item_ids(tpl, sections)
        _limit_diagrams(sections, opts)
        _assign_ids(sections)
        return Plan(
            title=inp.spec_title,
            summary=str(data.get("summary", "")).strip()[:500] or tpl.description,
            sections=sections,
            open_questions=clean_list(data.get("open_questions"), limit=15),
            terms=parse_terms(data.get("terms")),
        )

    # --- 修訂模式 ---

    async def _plan_revise(self, inp: RunInput) -> Plan:
        doc = parse_markdown(inp.existing_content)
        existing_questions_body = ""
        kept = []
        for sec in doc.sections:
            if normalize_heading(sec.heading) == _OPEN_Q_KEY:
                existing_questions_body = sec.body  # 整段保留（可能有表格、說明文字）
            else:
                kept.append(sec)
        if not kept:
            await self.log("原文件沒有章節（## 標題），改用撰寫模式，並把原內容當成參考", "warn")
            inp.requirement = f"{inp.requirement}\n\n## 原本的文件內容（參考）\n{inp.existing_content}"
            inp.mode = "draft"
            return await self._plan_draft(inp)

        existing = [
            (f"E{i}", strip_heading_number(sec.heading), sec.body) for i, sec in enumerate(kept, start=1)
        ]
        async with self.step("plan", "規劃修訂方式") as info:
            data = await self.chat_json(
                "plan", revise_planner_messages(inp, existing), REVISE_PLAN_SCHEMA, max_tokens=2500
            )
            plan = self.build_revise_plan(inp, existing, data)
            plan.preamble = doc.preamble
            plan.existing_questions_body = existing_questions_body
            if data is None:
                await self.log("模型沒有產生可用的修訂計畫，改成每一節都依修訂需求檢查修改", "warn")
            info.output = plan.to_dict()
            counts = defaultdict(int)
            for s in plan.sections:
                counts[s.action] += 1
            removed = len(existing) - counts["keep"] - counts["update"]
            info.message = (
                f"修訂計畫：修改 {counts['update']} 節、新增 {counts['write']} 節、刪除 {removed} 節、"
                f"保留 {counts['keep']} 節"
            )
        return plan

    @staticmethod
    def build_revise_plan(
        inp: RunInput, existing: list[tuple[str, str, str]], data: dict[str, Any] | None
    ) -> Plan:
        opts = inp.options
        if data is None:
            sections = [
                SectionPlan(
                    id="",
                    heading=heading,
                    goal="依修訂需求檢查並修改",
                    action="update",
                    source_body=body,
                    instruction="依照修訂需求修改這一節；如果修訂需求跟這一節無關，就照原樣輸出。",
                )
                for _sid, heading, body in existing
            ]
            _attach_item_ids(inp.template, sections)
            _assign_ids(sections)
            return Plan(title=inp.spec_title, summary="依修訂需求更新文件", sections=sections, fallback=True)

        headings = {sid: heading for sid, heading, _b in existing}
        changes: dict[str, tuple[str, str]] = {}
        for raw in data.get("changes") or []:
            if not isinstance(raw, dict):
                continue
            sid = normalize_sid(raw.get("section_id"), "E", len(existing))
            action = str(raw.get("action", "")).strip().lower()
            if sid is None or action not in ("update", "remove"):
                continue
            if (
                action == "remove"
                and inp.template.fixed_sections
                and inp.template.section_for(headings[sid]) is not None
            ):
                continue  # 章節固定的文件，不刪範本裡的章節
            changes[sid] = (action, str(raw.get("instruction", "")).strip()[:600])

        new_after: dict[str, list[SectionPlan]] = defaultdict(list)
        existing_keys = {normalize_heading(h) for _s, h, _b in existing}
        for raw in data.get("new_sections") or []:
            if not isinstance(raw, dict):
                continue
            heading = strip_heading_number(str(raw.get("heading", "")).strip())[:80]
            key = normalize_heading(heading)
            if not heading or key == _OPEN_Q_KEY or key in existing_keys:
                continue
            existing_keys.add(key)
            after_raw = str(raw.get("after", "")).strip().upper()
            after = "END" if after_raw == "END" else (normalize_sid(after_raw, "E", len(existing)) or "END")
            new_after[after].append(
                SectionPlan(
                    id="",
                    heading=heading,
                    goal=str(raw.get("goal", "")).strip()[:300],
                    key_points=clean_list(raw.get("key_points"), limit=8, max_len=200),
                    diagrams=_diagrams(raw.get("diagrams"), opts),
                    action="write",
                )
            )

        sections: list[SectionPlan] = []
        for sid, heading, body in existing:
            action, instruction = changes.get(sid, ("keep", ""))
            if action == "update":
                sections.append(
                    SectionPlan(
                        id="",
                        heading=heading,
                        goal=instruction,
                        action="update",
                        source_body=body,
                        instruction=instruction or "依照修訂需求修改這一節。",
                    )
                )
            elif action == "keep":
                sections.append(SectionPlan(id="", heading=heading, action="keep", source_body=body))
            sections.extend(new_after.pop(sid, []))
        for rest in new_after.values():
            sections.extend(rest)
        _attach_item_ids(inp.template, sections)
        _limit_diagrams(sections, opts)
        _assign_ids(sections)
        return Plan(
            title=inp.spec_title,
            summary=str(data.get("summary", "")).strip()[:500] or "依修訂需求更新文件",
            sections=sections,
            open_questions=clean_list(data.get("open_questions"), limit=15),
        )
