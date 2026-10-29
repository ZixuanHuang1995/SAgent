"""agent 共用的部分：呼叫模型、記錄步驟、統計 token。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from .llm import LLMClient, LLMResult
from .prompts import json_retry_message, json_too_long_message
from .textutil import parse_json_object
from .types import AgentStats
from .zh import to_traditional


class Emitter(Protocol):
    """把執行過程送到畫面（和資料庫）。"""

    async def emit(
        self, type: str, agent: str, message: str = "", data: dict[str, Any] | None = None
    ) -> None: ...

    def token(self, agent: str, step: str, text: str) -> None: ...


class NullEmitter:
    async def emit(
        self, type: str, agent: str, message: str = "", data: dict[str, Any] | None = None
    ) -> None:
        return None

    def token(self, agent: str, step: str, text: str) -> None:
        return None


class ListEmitter:
    """測試用：把事件存在 list。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.tokens: list[tuple[str, str, str]] = []

    async def emit(
        self, type: str, agent: str, message: str = "", data: dict[str, Any] | None = None
    ) -> None:
        self.events.append({"type": type, "agent": agent, "message": message, "data": data or {}})

    def token(self, agent: str, step: str, text: str) -> None:
        self.tokens.append((agent, step, text))


@dataclass
class StepInfo:
    step_id: str
    label: str
    message: str = ""
    output: dict[str, Any] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_eval_s: float = 0.0
    eval_s: float = 0.0
    load_s: float = 0.0
    context_length: int = 0

    def usage(self) -> dict[str, Any]:
        """步驟結束時記進紀錄的用量。時間拆解用來看花在讀提示詞（prefill）還是生成（decode）。"""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "prompt_eval_ms": int(self.prompt_eval_s * 1000),
            "eval_ms": int(self.eval_s * 1000),
            "load_ms": int(self.load_s * 1000),
            "context_length": self.context_length,
        }


class AgentBase:
    role = "agent"

    def __init__(self, llm: LLMClient, emitter: Emitter, stats: dict[str, AgentStats]):
        self.llm = llm
        self.emitter = emitter
        self.stats = stats.setdefault(self.role, AgentStats())
        self._step: StepInfo | None = None

    async def log(self, message: str, level: str = "info", **data: Any) -> None:
        await self.emitter.emit("log", self.role, message, {"level": level, **data})

    @asynccontextmanager
    async def step(self, step_id: str, label: str) -> AsyncIterator[StepInfo]:
        info = StepInfo(step_id=step_id, label=label)
        self._step = info
        started = time.monotonic()
        await self.emitter.emit("step_start", self.role, label, {"step": step_id})
        try:
            yield info
        except Exception as exc:
            self._step = None
            await self.emitter.emit(
                "step_end",
                self.role,
                f"{label}：失敗",
                {
                    "step": step_id,
                    "ok": False,
                    "error": str(exc),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    **info.usage(),
                },
            )
            raise
        self._step = None
        await self.emitter.emit(
            "step_end",
            self.role,
            info.message or label,
            {
                "step": step_id,
                "ok": True,
                "duration_ms": int((time.monotonic() - started) * 1000),
                **info.usage(),
                "output": info.output,
            },
        )

    async def chat(
        self,
        step_id: str,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        def on_token(text: str) -> None:
            self.emitter.token(self.role, step_id, text)

        result = await self.llm.chat(
            messages,
            schema=schema,
            temperature=temperature,
            max_tokens=max_tokens,
            on_token=on_token,
            purpose=f"{self.role}:{step_id}",
        )
        # 小模型偶爾會混入簡體字
        result.text = to_traditional(result.text)
        self.stats.calls += 1
        self.stats.prompt_tokens += result.prompt_tokens
        self.stats.completion_tokens += result.completion_tokens
        self.stats.seconds += result.duration_s
        self.stats.prompt_eval_seconds += result.prompt_eval_s
        self.stats.eval_seconds += result.eval_s
        self.stats.load_seconds += result.load_s
        if self._step is not None:
            self._step.prompt_tokens += result.prompt_tokens
            self._step.completion_tokens += result.completion_tokens
            self._step.prompt_eval_s += result.prompt_eval_s
            self._step.eval_s += result.eval_s
            self._step.load_s += result.load_s
            self._step.context_length = result.context_length or self._step.context_length
        await self._check_context(step_id, result, max_tokens)
        if result.truncated:
            # 記下結尾，才看得出是內容真的太長，還是模型一直重複同一段
            await self.log(
                "模型輸出達到長度上限，內容可能不完整", "warn", step=step_id, tail=result.text[-300:]
            )
        return result

    async def _check_context(self, step_id: str, result: LLMResult, max_tokens: int | None) -> None:
        """單次呼叫的輸入加上輸出上限超過上下文長度時警告。

        超過時 Ollama 會截掉提示詞或提早停止輸出，不會報錯；
        qwen3.6 這類混合架構也不能滑動上下文。只看單次呼叫，步驟的 token 數會把重試加在一起。
        """
        limit = result.context_length
        if not limit:
            return
        need = result.prompt_tokens + (max_tokens or 0)
        if need > limit:
            await self.log(
                f"輸入 {result.prompt_tokens} tokens 加上輸出上限 {max_tokens or 0}，"
                f"超過模型的上下文長度 {limit}，內容可能被截斷。請在設定頁調高 Context 長度",
                "warn",
                step=step_id,
                prompt_tokens=result.prompt_tokens,
                context_length=limit,
            )

    async def chat_json(
        self,
        step_id: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> dict[str, Any] | None:
        """要求 JSON 輸出。格式錯誤時重試一次，還是不行就回傳 None，由呼叫端改用備案。"""
        for attempt in range(2):
            result = await self.chat(
                step_id, messages, schema=schema, temperature=temperature, max_tokens=max_tokens
            )
            try:
                return parse_json_object(result.text)
            except ValueError as exc:
                if attempt == 0 and result.truncated:
                    # 被截斷的輸出放回去也沒用，照原樣重試又會被截斷，改成要求精簡
                    await self.log("模型輸出太長被截斷，請模型精簡後重試一次", "warn", step=step_id)
                    messages = [*messages, json_too_long_message()]
                elif attempt == 0:
                    await self.log(f"模型輸出的 JSON 格式不正確（{exc}），重試一次", "warn", step=step_id)
                    messages = [
                        *messages,
                        {"role": "assistant", "content": result.text[:4000]},
                        json_retry_message(),
                    ]
        await self.log("模型兩次都沒有輸出正確的 JSON，改用備案", "warn", step=step_id)
        return None
