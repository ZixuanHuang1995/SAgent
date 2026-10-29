import asyncio
import json

import httpx
import pytest

from sagent.agents.llm import LLMError, OllamaClient


def make_transport(capabilities, chunks, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": capabilities})
        if request.url.path == "/api/chat":
            seen.append(json.loads(request.content))
            body = "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks)
            return httpx.Response(200, content=body.encode("utf-8"))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_streaming_and_payload():
    seen = []
    chunks = [
        {"message": {"content": "你好"}, "done": False},
        {"message": {"content": "，世界"}, "done": False},
        {
            "message": {"content": ""},
            "done": True,
            "done_reason": "length",
            "prompt_eval_count": 12,
            "eval_count": 5,
        },
    ]
    client = OllamaClient(
        "http://x",
        "qwen3:8b",
        num_ctx=8192,
        transport=make_transport(["completion", "thinking"], chunks, seen),
    )
    tokens = []

    async def go():
        try:
            return await client.chat(
                [{"role": "user", "content": "hi"}],
                schema={"type": "object"},
                temperature=0.2,
                max_tokens=100,
                on_token=tokens.append,
            )
        finally:
            await client.close()

    result = asyncio.run(go())
    assert result.text == "你好，世界"
    assert tokens == ["你好", "，世界"]
    assert (result.prompt_tokens, result.completion_tokens, result.truncated) == (12, 5, True)
    payload = seen[0]
    assert payload["think"] is False
    assert payload["format"] == {"type": "object"}
    assert payload["options"] == {"temperature": 0.2, "num_predict": 100, "num_ctx": 8192}
    assert payload["stream"] is True and payload["keep_alive"] == "30m"


def test_no_think_param_for_models_without_thinking():
    seen = []
    chunks = [{"message": {"content": "ok"}, "done": True}]
    client = OllamaClient("http://x", "llama", transport=make_transport(["completion"], chunks, seen))

    async def go():
        try:
            await client.chat([{"role": "user", "content": "hi"}])
        finally:
            await client.close()

    asyncio.run(go())
    assert "think" not in seen[0]
    assert "num_ctx" not in seen[0]["options"]


def test_timings_and_context_length_from_ps():
    chunks = [
        {"message": {"content": "ok"}, "done": False},
        {
            "message": {"content": ""},
            "done": True,
            "prompt_eval_count": 900,
            "eval_count": 10,
            "prompt_eval_duration": 40_000_000_000,
            "eval_duration": 2_500_000_000,
            "load_duration": 500_000_000,
        },
    ]
    ps_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        if request.url.path == "/api/ps":
            ps_calls.append(1)
            models = [{"name": "other", "context_length": 4096}, {"name": "m", "context_length": 32768}]
            return httpx.Response(200, json={"models": models})
        body = "\n".join(json.dumps(c) for c in chunks)
        return httpx.Response(200, content=body.encode("utf-8"))

    client = OllamaClient("http://x", "m", transport=httpx.MockTransport(handler))

    async def go():
        try:
            first = await client.chat([{"role": "user", "content": "hi"}])
            second = await client.chat([{"role": "user", "content": "hi"}])
            return first, second
        finally:
            await client.close()

    first, second = asyncio.run(go())
    assert (first.prompt_eval_s, first.eval_s, first.load_s) == (40.0, 2.5, 0.5)
    assert first.context_length == second.context_length == 32768
    assert len(ps_calls) == 1  # 查到後就記住


def test_context_length_unknown_when_ps_fails():
    chunks = [{"message": {"content": "ok"}, "done": True}]
    client = OllamaClient("http://x", "m", transport=make_transport([], chunks, []))

    async def go():
        try:
            return await client.chat([{"role": "user", "content": "hi"}])
        finally:
            await client.close()

    assert asyncio.run(go()).context_length == 0


def test_error_in_stream():
    chunks = [{"error": "model crashed"}]
    client = OllamaClient("http://x", "m", transport=make_transport([], chunks, []))

    async def go():
        try:
            await client.chat([{"role": "user", "content": "hi"}])
        finally:
            await client.close()

    with pytest.raises(LLMError, match="model crashed"):
        asyncio.run(go())


def test_missing_model():
    transport = httpx.MockTransport(lambda r: httpx.Response(404, json={"error": "not found"}))
    client = OllamaClient("http://x", "nope", transport=transport)

    async def go():
        try:
            await client.chat([{"role": "user", "content": "hi"}])
        finally:
            await client.close()

    with pytest.raises(LLMError, match="ollama pull nope"):
        asyncio.run(go())
