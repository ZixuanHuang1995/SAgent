"""呼叫本機模型（Ollama）。

設計重點（CPU 推論很慢）：
- 一律串流，讓畫面看得到進度
- 預設關閉 thinking，省時間
- 用 keep_alive 讓模型留在記憶體，避免每次呼叫都重新載入
- JSON 輸出用 Ollama 的 format（JSON Schema），小模型比較不會寫壞
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

TokenCallback = Callable[[str], None]


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_s: float = 0.0
    truncated: bool = False  # 達到長度上限被截斷


class LLMClient(Protocol):
    model: str

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_token: TokenCallback | None = None,
        purpose: str = "",
    ) -> LLMResult: ...

    async def close(self) -> None: ...


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        num_ctx: int = 0,
        think: bool = False,
        keep_alive: str = "30m",
        timeout_s: float = 900,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.think = think
        self.keep_alive = keep_alive
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=10, read=timeout_s, write=60, pool=10),
            transport=transport,
        )
        self._capabilities: list[str] | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def capabilities(self) -> list[str]:
        if self._capabilities is None:
            try:
                resp = await self._client.post("/api/show", json={"model": self.model})
            except httpx.HTTPError as exc:
                raise LLMError(f"連不到 Ollama（{self.base_url}）：{exc}") from exc
            if resp.status_code == 404:
                raise LLMError(f"Ollama 沒有模型 {self.model}，請先執行 ollama pull {self.model}")
            if resp.status_code != 200:
                raise LLMError(f"Ollama 回應錯誤：HTTP {resp.status_code} {resp.text[:200]}")
            self._capabilities = list(resp.json().get("capabilities") or [])
        return self._capabilities

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_token: TokenCallback | None = None,
        purpose: str = "",
    ) -> LLMResult:
        options: dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens:
            options["num_predict"] = max_tokens
        if self.num_ctx:
            options["num_ctx"] = self.num_ctx
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": options,
        }
        if schema is not None:
            payload["format"] = schema
        if "thinking" in await self.capabilities():
            payload["think"] = self.think

        parts: list[str] = []
        result = LLMResult(text="")
        started = time.monotonic()
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise LLMError(f"Ollama 回應錯誤：HTTP {resp.status_code} {body[:300]}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise LLMError(f"Ollama 錯誤：{data['error']}")
                    chunk = (data.get("message") or {}).get("content") or ""
                    if chunk:
                        parts.append(chunk)
                        if on_token:
                            on_token(chunk)
                    if data.get("done"):
                        result.prompt_tokens = int(data.get("prompt_eval_count") or 0)
                        result.completion_tokens = int(data.get("eval_count") or 0)
                        result.truncated = data.get("done_reason") == "length"
        except httpx.TimeoutException as exc:
            raise LLMError("模型回應逾時，CPU 推論可能太慢，可以改用較小的模型") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"連不到 Ollama（{self.base_url}）：{exc}") from exc
        result.text = "".join(parts)
        result.duration_s = time.monotonic() - started
        return result


async def ollama_list_models(base_url: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=10) as client:
        resp = await client.get("/api/tags")
        resp.raise_for_status()
        models = []
        for m in resp.json().get("models", []):
            details = m.get("details") or {}
            models.append(
                {
                    "name": m.get("name") or m.get("model"),
                    "size": m.get("size", 0),
                    "parameter_size": details.get("parameter_size", ""),
                    "quantization": details.get("quantization_level", ""),
                    "family": details.get("family", ""),
                    "capabilities": m.get("capabilities") or [],
                }
            )
        return models


async def ollama_version(base_url: str) -> str:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=5) as client:
        resp = await client.get("/api/version")
        resp.raise_for_status()
        return resp.json().get("version", "")
