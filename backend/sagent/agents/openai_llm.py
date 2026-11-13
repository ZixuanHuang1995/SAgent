"""呼叫雲端模型：OpenAI 相容的 Chat Completions API。

xAI Grok、Gemini、Groq、OpenRouter、Ollama 雲端的 /v1 和公司內部的模型閘道都是這個格式，
網址、金鑰、模型名稱換掉就能接。跟 OllamaClient 一樣實作 LLMClient，agent 不用知道模型在哪裡。

各家支援的參數不完全一樣，所以：
- JSON 輸出用 response_format 的 json_schema；服務不支援時退到 json_object，再不行就不帶，
  交給 chat_json 的重試和備案
- 400 的錯誤訊息點名某個參數時（max_tokens、seed、temperature、stream_options），換掉或拿掉再試，
  同一個用戶端之後的呼叫都照這個結果
- 429 和 5xx 等一下再試，最多 3 次（免費方案常有每分鐘的上限）

金鑰只放在 Authorization 標頭，不寫進紀錄和工作選項；錯誤訊息裡出現金鑰時會遮掉。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .llm import LLMError, LLMResult, TokenCallback

RETRY_STATUS = {429, 500, 502, 503, 504, 529}
MAX_RETRIES = 3
RETRY_WAITS = (2.0, 5.0, 15.0)
MAX_RETRY_WAIT = 60.0
# 400 的訊息提到這些字時是輸入太長之類的問題，不是 response_format 的問題
_NOT_FORMAT_HINTS = ("context", "token", "too long", "too large")


def error_detail(body: str) -> str:
    """錯誤回應裡給人看的那一句。格式通常是 {"error": {"message": "…"}}，不是的話照原文（最多 300 字）。

    Gemini 的相容端點會多包一層陣列：[{"error": {…}}]。
    """
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()[:300]
    if isinstance(data, list) and data:
        data = data[0]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    if isinstance(err, str):
        return err[:300]
    return body.strip()[:300]


def status_message(status: int, detail: str, model: str = "") -> str:
    """HTTP 狀態碼轉成給使用者看的說明。"""
    tail = f"：{detail}" if detail else ""
    if status in (401, 403):
        return f"雲端 API 拒絕存取（HTTP {status}），金鑰不正確或沒有權限，請檢查 SAGENT_CLOUD_API_KEY{tail}"
    if status == 404:
        what = f"找不到模型 {model}" if model else "找不到這個網址"
        return f"雲端 API {what}（HTTP 404），請確認模型名稱和 SAGENT_CLOUD_BASE_URL{tail}"
    if status == 429:
        return f"雲端 API 的用量或速率超過上限（HTTP 429），等一下再試{tail}"
    return f"雲端 API 回應錯誤：HTTP {status}{tail}"


def _retry_wait(resp: httpx.Response, attempt: int) -> float:
    """服務有說要等多久（Retry-After，秒）就照它，沒有就用固定的間隔。最多等 60 秒。"""
    try:
        wait = float(resp.headers.get("retry-after", ""))
    except ValueError:
        wait = RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)]
    return max(0.0, min(wait, MAX_RETRY_WAIT))


class OpenAICompatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_s: float = 300,
        transport: httpx.AsyncBaseTransport | None = None,
        seed: int | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.seed = seed
        self._api_key = api_key
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(connect=10, read=timeout_s, write=60, pool=10),
            transport=transport,
        )
        # 這個服務接受哪些參數，碰到 400 才知道，之後的呼叫照用
        self._json_mode = "json_schema"  # → "json_object" → ""（不帶 response_format）
        self._max_tokens_key = "max_tokens"
        self._dropped: set[str] = set()

    async def close(self) -> None:
        await self._client.aclose()

    async def environment(self) -> dict[str, Any]:
        """記在工作選項裡的執行環境。雲端沒有 digest，同一個模型名稱背後換了版本看不出來。"""
        return {"provider": "openai_compat", "base_url": self.base_url}

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    def _payload(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True}
        if "stream_options" not in self._dropped:
            payload["stream_options"] = {"include_usage": True}  # 串流時要這個才會回報 token 數
        if temperature is not None and "temperature" not in self._dropped:
            payload["temperature"] = temperature
        if max_tokens:
            payload[self._max_tokens_key] = max_tokens
        if self.seed is not None and "seed" not in self._dropped:
            payload["seed"] = self.seed
        if schema is not None and self._json_mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": schema},
            }
        elif schema is not None and self._json_mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _adapt(self, status: int, body: str, payload: dict[str, Any]) -> bool:
        """服務不接受某個參數時調整設定。回傳 True 表示改過了，可以再試一次。"""
        if status not in (400, 422):
            return False
        text = body.lower()
        if (
            "max_completion_tokens" in text
            and self._max_tokens_key == "max_tokens"
            and "max_tokens" in payload
        ):
            self._max_tokens_key = "max_completion_tokens"  # OpenAI 較新的模型只收這個名稱
            return True
        for name in ("stream_options", "seed", "temperature"):
            if name in payload and name in text:
                self._dropped.add(name)
                return True
        if "response_format" in payload:
            named = "response_format" in text or "json_schema" in text or "schema" in text
            if named or not any(hint in text for hint in _NOT_FORMAT_HINTS):
                self._json_mode = "json_object" if self._json_mode == "json_schema" else ""
                return True
        return False

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
        started = time.monotonic()
        retries = 0
        try:
            while True:
                payload = self._payload(messages, schema, temperature, max_tokens)
                async with self._client.stream("POST", "chat/completions", json=payload) as resp:
                    if resp.status_code == 200:
                        result = await self._read_stream(resp, on_token)
                        result.duration_s = time.monotonic() - started
                        return result
                    body = self._redact((await resp.aread()).decode("utf-8", errors="replace"))
                    if self._adapt(resp.status_code, body, payload):
                        continue
                    if resp.status_code in RETRY_STATUS and retries < MAX_RETRIES:
                        wait = _retry_wait(resp, retries)
                        retries += 1
                    else:
                        raise LLMError(status_message(resp.status_code, error_detail(body), self.model))
                await self._sleep(wait)
        except httpx.TimeoutException as exc:
            raise LLMError("雲端 API 回應逾時，等一下再試") from exc
        except httpx.HTTPError as exc:
            reason = self._redact(str(exc)) or exc.__class__.__name__
            raise LLMError(f"連不到雲端 API（{self.base_url}）：{reason}") from exc

    async def _read_stream(self, resp: httpx.Response, on_token: TokenCallback | None) -> LLMResult:
        """讀 SSE：每一行是「data: {…}」，最後一行是「data: [DONE]」。"""
        parts: list[str] = []
        result = LLMResult(text="")
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue  # 空行、註解（OpenRouter 會送「: OPENROUTER PROCESSING」）
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("error"):
                raise LLMError(f"雲端 API 錯誤：{self._redact(error_detail(data))}")
            usage = obj.get("usage")
            if isinstance(usage, dict):
                result.prompt_tokens = int(usage.get("prompt_tokens") or 0)
                result.completion_tokens = int(usage.get("completion_tokens") or 0)
            for choice in obj.get("choices") or []:
                chunk = (choice.get("delta") or {}).get("content")
                if isinstance(chunk, str) and chunk:
                    parts.append(chunk)
                    if on_token:
                        on_token(chunk)
                if choice.get("finish_reason") == "length":
                    result.truncated = True
        result.text = "".join(parts)
        return result


async def cloud_list_models(
    base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None
) -> list[str]:
    """這個服務有哪些模型（GET /models）。連不到或回應不對時丟 httpx.HTTPError、ValueError。"""
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
        transport=transport,
    ) as client:
        resp = await client.get("models")
        resp.raise_for_status()
        data = resp.json().get("data") or []
        return sorted({str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")})
