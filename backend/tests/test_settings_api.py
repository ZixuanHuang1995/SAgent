import httpx

from sagent.api import system
from sagent.services.validation import readable_errors


def test_validation_messages_are_chinese(client):
    r = client.put("/api/settings", json={"temperature": 3})
    assert r.status_code == 422
    assert r.json()["detail"] == "輸入資料不正確：溫度不能大於 1.5"
    detail = client.put("/api/settings", json={"num_ctx": "abc", "max_revisions": -1}).json()["detail"]
    assert "Context 長度要填整數" in detail and "修訂輪數不能小於 0" in detail
    detail = client.put("/api/settings", json={"uml_syntax": "x"}).json()["detail"]
    assert "UML 圖語法只能是 'auto'、'mermaid'、'plantuml'" in detail
    # 沒有改到任何設定
    assert client.get("/api/settings").json()["temperature"] == 0.4


def test_unknown_errors_keep_original_text():
    text = readable_errors([{"type": "something_new", "loc": ("body", "foo"), "msg": "Bad thing"}])
    assert text == "輸入資料不正確：foo：Bad thing"


def test_ollama_url_must_be_http(client):
    r = client.put("/api/settings", json={"ollama_url": "127.0.0.1:11434"})
    assert r.status_code == 400
    assert "http://" in r.json()["detail"]
    s = client.put("/api/settings", json={"ollama_url": " http://10.0.0.5:11434/ "}).json()
    assert s["ollama_url"] == "http://10.0.0.5:11434"


def test_health_shows_data_dir(client, cfg):
    health = client.get("/api/health").json()
    assert health["data_dir"] == str(cfg.data_dir.resolve())


def test_ollama_check_uses_given_url(client, monkeypatch):
    seen = []

    async def fake_version(url):
        seen.append(url)
        return "0.12.0"

    async def fake_models(url):
        return [{"name": "qwen3:8b", "capabilities": ["completion"]}]

    monkeypatch.setattr(system, "ollama_version", fake_version)
    monkeypatch.setattr(system, "ollama_list_models", fake_models)
    r = client.post("/api/ollama/check", json={"url": "http://10.0.0.5:11434/"})
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is True and body["version"] == "0.12.0"
    assert [m["name"] for m in body["models"]] == ["qwen3:8b"]
    assert seen == ["http://10.0.0.5:11434"]
    # 只是測試，不會改設定
    assert client.get("/api/settings").json()["ollama_url"] != "http://10.0.0.5:11434"


def test_ollama_check_reports_errors(client, monkeypatch):
    async def refuse(url):
        raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(system, "ollama_version", refuse)
    body = client.post("/api/ollama/check", json={"url": "http://127.0.0.1:1"}).json()
    assert body["reachable"] is False
    assert "連不到" in body["error"] and body["models"] == []
    assert client.post("/api/ollama/check", json={"url": "ftp://x"}).status_code == 400


def test_plantuml_message_points_to_env_var(client):
    r = client.post("/api/render/plantuml", json={"source": "@startuml\nA->B\n@enduml"})
    assert r.status_code == 503
    assert "SAGENT_PLANTUML_SERVER" in r.json()["detail"]
