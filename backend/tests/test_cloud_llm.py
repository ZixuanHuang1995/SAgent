"""雲端模型：OpenAI 相容的用戶端、設定、建立工作和系統狀態。不連真的服務。"""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from conftest import wait_run
from fastapi.testclient import TestClient

from sagent.agents.fake_llm import FakeLLM
from sagent.agents.llm import LLMError, OllamaClient
from sagent.agents.openai_llm import OpenAICompatClient, cloud_list_models
from sagent.api import system
from sagent.app_state import AppState
from sagent.main import create_app

KEY = "sk-test-not-a-real-key"
BASE = "https://cloud.test/v1"


def sse(*objs) -> bytes:
    lines = [f"data: {json.dumps(o, ensure_ascii=False)}" for o in objs] + ["data: [DONE]"]
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


def delta(text: str, finish: str | None = None) -> dict:
    return {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}]}


OK_STREAM = sse(delta("好"), {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}})


def run_chat(client: OpenAICompatClient, **kwargs):
    async def go():
        try:
            return await client.chat([{"role": "user", "content": "hi"}], **kwargs)
        finally:
            await client.close()

    return asyncio.run(go())


def make_client(handler, **kwargs) -> OpenAICompatClient:
    return OpenAICompatClient(BASE, KEY, "grok-test", transport=httpx.MockTransport(handler), **kwargs)


def test_streaming_and_payload():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = sse(
            delta("你好"),
            delta("，世界", finish="length"),
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
        )
        return httpx.Response(200, content=body)

    tokens = []
    result = run_chat(
        make_client(handler, seed=7),
        schema={"type": "object"},
        temperature=0.2,
        max_tokens=100,
        on_token=tokens.append,
    )
    assert result.text == "你好，世界"
    assert tokens == ["你好", "，世界"]
    assert (result.prompt_tokens, result.completion_tokens, result.truncated) == (12, 5, True)
    # 雲端沒有上下文長度和時間拆解
    assert (result.context_length, result.prompt_eval_s, result.eval_s) == (0, 0.0, 0.0)
    request = seen[0]
    assert str(request.url) == f"{BASE}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    payload = json.loads(request.content)
    assert payload == {
        "model": "grok-test",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0.2,
        "max_tokens": 100,
        "seed": 7,
        "response_format": {"type": "json_schema", "json_schema": {"name": "output", "schema": {"type": "object"}}},
    }


def test_json_schema_falls_back_when_service_rejects_it():
    """不支援 json_schema 的服務：退到 json_object，再不行就不帶，之後的呼叫照這個結果。"""
    formats = []

    def handler(request: httpx.Request) -> httpx.Response:
        fmt = json.loads(request.content).get("response_format")
        formats.append(fmt["type"] if fmt else None)
        if fmt:
            return httpx.Response(400, json={"error": {"message": "response_format is not supported"}})
        return httpx.Response(200, content=OK_STREAM)

    client = make_client(handler)

    async def go():
        try:
            first = await client.chat([{"role": "user", "content": "hi"}], schema={"type": "object"})
            await client.chat([{"role": "user", "content": "hi"}], schema={"type": "object"})
            return first
        finally:
            await client.close()

    assert asyncio.run(go()).text == "好"
    assert formats == ["json_schema", "json_object", None, None]


def test_context_error_does_not_drop_json_schema():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "maximum context length exceeded"}})

    with pytest.raises(LLMError, match="maximum context length"):
        run_chat(make_client(handler), schema={"type": "object"})
    assert len(calls) == 1


def test_switches_to_max_completion_tokens_and_drops_rejected_params():
    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if "max_tokens" in payload:
            message = "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."
            return httpx.Response(400, json={"error": {"message": message}})
        if "temperature" in payload:
            return httpx.Response(400, json={"error": {"message": "temperature does not support 0.2"}})
        return httpx.Response(200, content=OK_STREAM)

    result = run_chat(make_client(handler), temperature=0.2, max_tokens=50)
    assert result.text == "好"
    assert len(payloads) == 3
    assert payloads[-1]["max_completion_tokens"] == 50
    assert "max_tokens" not in payloads[-1] and "temperature" not in payloads[-1]


def test_retries_rate_limit_then_succeeds():
    calls = []
    waits = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "7"}, json={"error": {"message": "slow down"}})
        if len(calls) == 2:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, content=OK_STREAM)

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    assert run_chat(make_client(handler, sleep=fake_sleep)).text == "好"
    assert waits == [7.0, 5.0]  # 第一次照 Retry-After，第二次用固定的間隔


def test_rate_limit_gives_up_after_retries():
    calls = []

    async def fake_sleep(seconds: float) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json={"error": {"message": "quota exceeded"}})

    with pytest.raises(LLMError, match="超過上限.*quota exceeded"):
        run_chat(make_client(handler, sleep=fake_sleep))
    assert len(calls) == 4  # 第一次加上重試 3 次


def test_auth_error_hides_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {KEY}"}})

    with pytest.raises(LLMError) as exc:
        run_chat(make_client(handler))
    assert "SAGENT_CLOUD_API_KEY" in str(exc.value)
    assert KEY not in str(exc.value) and "***" in str(exc.value)


def test_missing_model_and_stream_error():
    with pytest.raises(LLMError, match="找不到模型 grok-test"):
        run_chat(make_client(lambda r: httpx.Response(404, json={"error": {"message": "no such model"}})))
    body = sse(delta("寫到一半"), {"error": {"message": "server overloaded"}})
    with pytest.raises(LLMError, match="server overloaded"):
        run_chat(make_client(lambda r: httpx.Response(200, content=body)))


def test_error_wrapped_in_array():
    """Gemini 的相容端點把錯誤包在陣列裡，訊息要照樣讀得出來，不是整段 JSON。"""
    body = [{"error": {"code": 404, "message": "This model is no longer available. Use gemini-next."}}]
    with pytest.raises(LLMError) as exc:
        run_chat(make_client(lambda r: httpx.Response(404, json=body)))
    text = str(exc.value)
    assert "找不到模型 grok-test" in text and "Use gemini-next." in text
    assert "[{" not in text


def test_environment_has_no_key():
    client = make_client(lambda r: httpx.Response(200, content=OK_STREAM))

    async def go():
        try:
            return await client.environment()
        finally:
            await client.close()

    assert asyncio.run(go()) == {"provider": "openai_compat", "base_url": BASE}


def test_list_models():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{BASE}/models"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, json={"data": [{"id": "grok-b"}, {"id": "grok-a"}, {"object": "x"}]})

    models = asyncio.run(cloud_list_models(BASE, KEY, transport=httpx.MockTransport(handler)))
    assert models == ["grok-a", "grok-b"]


# --- 設定、建立工作、系統狀態 ---


@pytest.fixture
def cloud_cfg(cfg):
    """接真模型的啟動設定（不是示範模式），而且有設雲端 API 的網址和金鑰。"""
    return replace(cfg, llm_provider="ollama", cloud_base_url=BASE, cloud_api_key=KEY)


@pytest.fixture
def cloud_client(cloud_cfg):
    """模型還是假的，但記下工作用哪個模型、哪些選項建立。"""
    made = []

    def factory(model, opts):
        made.append((model, dict(opts)))
        return FakeLLM(model=model)

    app = create_app(cloud_cfg, llm_factory=factory)
    with TestClient(app, headers={"X-SAgent-Client": "test"}) as c:
        c.made = made
        yield c


def _spec(client):
    project = client.post("/api/projects", json={"name": "訂單系統", "code": "ORD"}).json()
    tree = client.get(f"/api/projects/{project['id']}").json()
    body = {"phase_id": tree["phases"][0]["id"], "title": "下單功能", "init": "none"}
    return client.post(f"/api/projects/{project['id']}/specs", json=body).json()


def test_cloud_needs_env_and_model(client, cloud_client):
    # 沒有設環境變數：不能選雲端
    r = client.put("/api/settings", json={"llm_source": "cloud", "cloud_model": "grok-test"})
    assert r.status_code == 400 and "SAGENT_CLOUD_API_KEY" in r.json()["detail"]
    assert client.get("/api/settings").json()["llm_source"] == "ollama"
    # 有設：還要填模型
    r = cloud_client.put("/api/settings", json={"llm_source": "cloud"})
    assert r.status_code == 400 and "雲端模型" in r.json()["detail"]
    s = cloud_client.put("/api/settings", json={"llm_source": "cloud", "cloud_model": " grok-test "}).json()
    assert (s["llm_source"], s["cloud_model"]) == ("cloud", "grok-test")
    assert client.put("/api/settings", json={"llm_source": "x"}).status_code == 422


def test_run_uses_cloud_model(cloud_client):
    cloud_client.put(
        "/api/settings",
        json={"llm_source": "cloud", "cloud_model": "grok-test", "cloud_reviewer_model": "grok-review"},
    )
    spec = _spec(cloud_client)
    r = cloud_client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "客戶可以線上下單"})
    assert r.status_code == 201, r.text
    run = wait_run(cloud_client, r.json()["id"])
    assert run["status"] == "succeeded", run["error"]
    assert run["model"] == "grok-test"
    assert run["options"]["llm_source"] == "cloud"
    assert run["options"]["reviewer_model"] == "grok-review"
    assert KEY not in json.dumps(run)  # 金鑰不進工作紀錄
    assert [m for m, _ in cloud_client.made] == ["grok-test", "grok-review"]
    assert all(opts["llm_source"] == "cloud" for _, opts in cloud_client.made)
    assert any("雲端 API" in e["message"] for e in run["events"] if e["type"] == "log")


def test_run_stays_local_by_default(cloud_client):
    """有設金鑰但沒切換：照樣用本機模型。"""
    spec = _spec(cloud_client)
    r = cloud_client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "客戶可以線上下單"})
    run = wait_run(cloud_client, r.json()["id"])
    assert run["options"]["llm_source"] == "ollama"
    assert run["model"] == cloud_client.get("/api/settings").json()["default_model"]
    assert not any("雲端 API" in e["message"] for e in run["events"])


def test_demo_mode_never_uses_cloud(cfg, tmp_path):
    """示範模式（假模型）：設定存的是雲端也不會用。"""
    demo = replace(cfg, cloud_base_url=BASE, cloud_api_key=KEY)
    app = create_app(demo, llm_factory=lambda model, opts: FakeLLM(model=model))
    with TestClient(app, headers={"X-SAgent-Client": "test"}) as c:
        c.put("/api/settings", json={"llm_source": "cloud", "cloud_model": "grok-test"})
        health = c.get("/api/health").json()
        assert health["llm_provider"] == "fake" and health["llm_source"] == "ollama"
        spec = _spec(c)
        r = c.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "需求"})
        run = wait_run(c, r.json()["id"])
        assert run["options"]["llm_source"] == "ollama" and run["model"] != "grok-test"


def test_run_refused_when_key_is_gone(cloud_cfg, tmp_path):
    """設定存的是雲端，重新啟動後沒有金鑰：不偷偷改用本機模型。"""
    app = create_app(cloud_cfg, llm_factory=lambda model, opts: FakeLLM(model=model))
    with TestClient(app, headers={"X-SAgent-Client": "test"}) as c:
        c.put("/api/settings", json={"llm_source": "cloud", "cloud_model": "grok-test"})
        spec = _spec(c)
    app = create_app(replace(cloud_cfg, cloud_api_key=""), llm_factory=lambda model, opts: FakeLLM(model=model))
    with TestClient(app, headers={"X-SAgent-Client": "test"}) as c:
        r = c.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "需求"})
        assert r.status_code == 503 and "SAGENT_CLOUD_API_KEY" in r.json()["detail"]


def test_default_factory_picks_client(cloud_cfg):
    state = AppState(cloud_cfg)

    async def go():
        cloud = state.default_llm_factory("grok-test", {"llm_source": "cloud", "seed": 3})
        local = state.default_llm_factory("qwen3:8b", {"llm_source": "ollama"})
        old = state.default_llm_factory("qwen3:8b", {})  # 舊的工作沒有記來源
        try:
            return cloud, local, old
        finally:
            for llm in (cloud, local, old):
                await llm.close()

    cloud, local, old = asyncio.run(go())
    assert isinstance(cloud, OpenAICompatClient) and (cloud.base_url, cloud.seed) == (BASE, 3)
    assert isinstance(local, OllamaClient) and isinstance(old, OllamaClient)
    state.cfg.cloud_api_key = ""
    with pytest.raises(LLMError, match="SAGENT_CLOUD_API_KEY"):
        state.default_llm_factory("grok-test", {"llm_source": "cloud"})
    state.engine.dispose()


def test_health_reports_cloud_without_key(client, cloud_client):
    health = client.get("/api/health").json()
    assert health["llm_source"] == "ollama"
    assert health["cloud"] == {"configured": False, "base_url": "", "model": ""}
    cloud_client.put("/api/settings", json={"llm_source": "cloud", "cloud_model": "grok-test"})
    text = cloud_client.get("/api/health").text
    health = json.loads(text)
    assert health["llm_source"] == "cloud"
    assert health["cloud"] == {"configured": True, "base_url": BASE, "model": "grok-test"}
    assert health["ollama"]["reachable"] is False  # 用雲端時不檢查 Ollama
    assert KEY not in text
    assert KEY not in cloud_client.get("/api/settings").text


def test_cloud_check(client, cloud_client, monkeypatch):
    body = client.post("/api/cloud/check").json()
    assert body["configured"] is False and "SAGENT_CLOUD_BASE_URL" in body["error"]

    seen = []

    async def fake_models(base_url, api_key):
        seen.append((base_url, api_key))
        return ["grok-a", "grok-b"]

    monkeypatch.setattr(system, "cloud_list_models", fake_models)
    body = cloud_client.post("/api/cloud/check").json()
    assert body["reachable"] is True and body["models"] == ["grok-a", "grok-b"]
    assert seen == [(BASE, KEY)]

    async def unauthorized(base_url, api_key):
        request = httpx.Request("GET", f"{base_url}/models")
        response = httpx.Response(401, request=request, json={"error": {"message": f"bad key {api_key}"}})
        raise httpx.HTTPStatusError("401", request=request, response=response)

    monkeypatch.setattr(system, "cloud_list_models", unauthorized)
    body = cloud_client.post("/api/cloud/check").json()
    assert body["reachable"] is False and "金鑰不正確" in body["error"] and KEY not in body["error"]

    async def no_list(base_url, api_key):
        request = httpx.Request("GET", f"{base_url}/models")
        raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))

    monkeypatch.setattr(system, "cloud_list_models", no_list)
    body = cloud_client.post("/api/cloud/check").json()
    assert body["reachable"] is True and body["models"] == [] and "自己填" in body["note"]
