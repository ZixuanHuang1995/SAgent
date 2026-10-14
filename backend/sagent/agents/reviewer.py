"""Reviewer：先做程式檢查（章節是否空白、圖表語法），再請模型對照需求審查內容。"""

from __future__ import annotations

import re
from typing import Any

from ..services.diagrams import extract_diagrams, lint_mermaid, lint_plantuml
from .base import AgentBase
from .prompts import REVIEW_SCHEMA, reviewer_messages
from .textutil import normalize_sid, text_length
from .types import Draft, Issue, Plan, Review, RunInput

_PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME|Lorem ipsum)\b|（待補充）", re.I)
_SEVERITIES = ("high", "medium", "low")


def static_checks(plan: Plan, draft: Draft) -> list[Issue]:
    issues: list[Issue] = []
    for sec in draft.sections:
        sec_plan = plan.section(sec.id)
        if sec_plan is not None and sec_plan.action == "keep":
            continue
        if text_length(sec.body) < 30:
            issues.append(Issue(sec.id, "high", "這一節內容太少", "依大綱的目標補上具體內容", source="check"))
        for idx, block in enumerate(extract_diagrams(sec.body)):
            errors = lint_plantuml(block.code) if block.lang == "plantuml" else lint_mermaid(block.code)
            if errors:
                issues.append(
                    Issue(
                        sec.id,
                        "high",
                        f"第 {idx + 1} 張圖語法錯誤：{'；'.join(errors[:3])}",
                        "照語法規則修正",
                        source="check",
                        diagram_index=idx,
                    )
                )
        if _PLACEHOLDER_RE.search(sec.body):
            issues.append(
                Issue(
                    sec.id,
                    "low",
                    "內文有未完成的標記（TODO、待補充等）",
                    "補上內容或移除標記",
                    source="check",
                )
            )
    return issues


def draft_for_review(plan: Plan, draft: Draft, only: set[str] | None = None) -> str:
    """給審查模型看的草稿：圖表程式碼換成一行說明，節省 token。only 有值時只放這些章節。"""
    parts = []
    for number, sec in enumerate(draft.sections, start=1):
        if only is not None and sec.id not in only:
            continue
        body = sec.body
        for block in reversed(extract_diagrams(body)):
            lines = body.splitlines()
            first = block.code.strip().splitlines()[0] if block.code.strip() else ""
            lines[block.start : block.end + 1] = [f"（{block.lang} 圖：{first[:40]}）"]
            body = "\n".join(lines)
        parts.append(f"[{sec.id}] ## {number}. {sec.heading}\n{body.strip()}")
    return "\n\n".join(parts)


def parse_llm_issues(data: dict[str, Any], plan: Plan) -> list[Issue]:
    issues: list[Issue] = []
    seen: set[tuple[str | None, str]] = set()
    for raw in data.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        problem = str(raw.get("problem", "")).strip()
        if not problem:
            continue
        severity = str(raw.get("severity", "medium")).strip().lower()
        if severity not in _SEVERITIES:
            severity = "medium"
        sid = normalize_sid(raw.get("section_id"), "S", len(plan.sections))
        key = (sid, problem)
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            Issue(sid, severity, problem[:400], str(raw.get("suggestion", "")).strip()[:400], source="llm")  # type: ignore[arg-type]
        )
    return issues[:12]


class Reviewer(AgentBase):
    role = "reviewer"

    async def review(
        self,
        inp: RunInput,
        plan: Plan,
        draft: Draft,
        round_no: int,
        focus: set[str] | None = None,
        previous: Review | None = None,
    ) -> Review:
        """focus 有值時（修訂後的再審查）只請模型看修改過的章節，其他章節沿用上一輪的意見。

        CPU 上讀整份草稿要好幾分鐘，只看修改過的章節可以省下大部分時間。
        """
        step = f"review:{round_no}"
        label = (
            "審查文件"
            if round_no == 1
            else f"第 {round_no} 次審查（只看修改過的章節）"
            if focus
            else f"第 {round_no} 次審查"
        )
        async with self.step(step, label) as info:
            checks = static_checks(plan, draft)
            notes = [f"[{i.section_id}] {i.problem}" for i in checks if i.severity != "low"]
            data = await self.chat_json(
                step,
                reviewer_messages(
                    inp, plan, draft_for_review(plan, draft, focus), notes, partial=bool(focus)
                ),
                REVIEW_SCHEMA,
                temperature=0.1,
                max_tokens=2000,
            )
            carried: list[Issue] = []
            if focus and previous is not None:
                carried = [i for i in previous.issues if i.source == "llm" and i.section_id not in focus]
            if data is None:
                review = Review(
                    round=round_no,
                    score=previous.score if previous else None,
                    summary="審查模型沒有給出可用的結果，只列出程式檢查的問題。",
                    issues=checks + carried,
                    llm_ok=False,
                )
            else:
                score = data.get("score")
                try:
                    score = max(1, min(10, int(score)))
                except (TypeError, ValueError):
                    score = previous.score if previous else None
                new_issues = parse_llm_issues(data, plan)
                if focus:
                    new_issues = [i for i in new_issues if i.section_id is None or i.section_id in focus]
                review = Review(
                    round=round_no,
                    score=score,
                    summary=str(data.get("summary", "")).strip()[:500],
                    issues=checks + new_issues + carried,
                )
            info.output = review.to_dict()
            high = sum(1 for i in review.issues if i.severity == "high")
            medium = sum(1 for i in review.issues if i.severity == "medium")
            score_text = f"{review.score} 分，" if review.score is not None else ""
            verdict = "需要修訂" if review.needs_revision else "通過"
            info.message = f"審查結果：{score_text}{verdict}（重要問題 {high} 個、一般問題 {medium} 個）"
        return review
