"""命令列直接跑一次 planner → writer → reviewer，不經過網頁。用來測試模型和提示詞。

用法（在專案根目錄）：
    uv run --project backend python scripts/run_pipeline.py --requirement-file 需求.txt --out 輸出.md
    uv run --project backend python scripts/run_pipeline.py --model qwen3.6:35b-a3b --template use_case --requirement "..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from sagent.agents.doc_templates import load_templates
from sagent.agents.llm import OllamaClient
from sagent.agents.pipeline import SpecPipeline
from sagent.agents.types import PipelineOptions, RunInput


class PrintEmitter:
    def __init__(self, log_path: Path | None):
        self.started = time.monotonic()
        self.log = log_path.open("w", encoding="utf-8") if log_path else None
        self.chars = 0

    def _out(self, text: str) -> None:
        stamp = f"[{time.monotonic() - self.started:7.1f}s]"
        print(f"{stamp} {text}", flush=True)
        if self.log:
            self.log.write(f"{stamp} {text}\n")
            self.log.flush()

    async def emit(self, type, agent, message="", data=None):
        data = data or {}
        if self.chars:
            print(f"  （串流 {self.chars} 字）", flush=True)
            self.chars = 0
        if type == "step_end":
            tok = f"in={data.get('prompt_tokens')} out={data.get('completion_tokens')}"
            self._out(f"{agent:8} ✔ {message}  {data.get('duration_ms', 0) / 1000:.1f}s {tok}")
            if self.log and data.get("output"):
                self.log.write(json.dumps(data["output"], ensure_ascii=False, indent=2) + "\n")
        elif type == "step_start":
            self._out(f"{agent:8} ▶ {message}")
        elif type in ("log", "phase"):
            self._out(f"{agent:8} · {message}")

    def token(self, agent, step, text):
        self.chars += len(text)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--template", default="functional_spec")
    parser.add_argument("--title", default="測試文件")
    parser.add_argument("--requirement", default="")
    parser.add_argument("--requirement-file", type=Path)
    parser.add_argument("--existing-file", type=Path, help="修訂模式：既有文件")
    parser.add_argument("--max-revisions", type=int, default=1)
    parser.add_argument("--max-diagrams", type=int, default=4)
    parser.add_argument("--uml", default="auto", choices=["auto", "mermaid", "plantuml"])
    parser.add_argument("--out", type=Path, default=Path("pipeline-output.md"))
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()

    requirement = args.requirement_file.read_text(encoding="utf-8") if args.requirement_file else args.requirement
    if not requirement.strip():
        print("請用 --requirement 或 --requirement-file 提供需求內容", file=sys.stderr)
        return 2
    existing = args.existing_file.read_text(encoding="utf-8") if args.existing_file else ""
    inp = RunInput(
        mode="revise" if existing else "draft",
        project_name="測試專案",
        project_description="",
        phase_name="需求分析",
        spec_title=args.title,
        template=load_templates()[args.template],
        requirement=requirement,
        existing_content=existing,
        options=PipelineOptions(uml_syntax=args.uml, max_revisions=args.max_revisions, max_diagrams=args.max_diagrams),
    )
    llm = OllamaClient(args.ollama, args.model)
    emitter = PrintEmitter(args.log)
    try:
        result = await SpecPipeline(llm, emitter).run(inp)
    finally:
        await llm.close()
    args.out.write_text(result.markdown, encoding="utf-8")
    emitter._out(f"完成，輸出：{args.out}")
    for name, st in result.stats.items():
        emitter._out(f"{name:8} 呼叫 {st.calls} 次，輸入 {st.prompt_tokens} / 輸出 {st.completion_tokens} tokens，{st.seconds:.0f}s")
    if result.review:
        emitter._out("最後審查：" + json.dumps(result.review.to_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
