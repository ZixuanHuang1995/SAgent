def test_empty_client_header_is_rejected(client):
    pid = client.post("/api/projects", json={"name": "安全測試"}).json()["id"]
    # 模擬其他網站的表單：送不出自訂標頭
    r = client.post(f"/api/projects/{pid}/phases", data={"name": "x"}, headers={"X-SAgent-Client": ""})
    assert r.status_code == 403
    assert (
        client.request("DELETE", f"/api/projects/{pid}", headers={"X-SAgent-Client": ""}).status_code == 403
    )
    assert client.get(f"/api/projects/{pid}").status_code == 200


def test_missing_header_is_rejected(cfg):
    from fastapi.testclient import TestClient

    from sagent.main import create_app

    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/projects").status_code == 200  # 讀取不受影響
        r = c.post("/api/projects", json={"name": "沒有標頭"})
        assert r.status_code == 403
        assert "X-SAgent-Client" in r.json()["detail"]
        assert c.post("/api/agent/runs/1/cancel").status_code == 403


def test_unknown_host_is_rejected(cfg):
    from fastapi.testclient import TestClient

    from sagent.main import create_app

    with TestClient(create_app(cfg), base_url="http://evil.example.com") as c:
        assert c.get("/api/projects").status_code == 400
