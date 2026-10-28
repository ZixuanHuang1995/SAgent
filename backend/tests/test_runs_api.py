import json

from conftest import wait_run, wait_status


def _spec(client, project, init="none"):
    body = {"phase_id": project["phases"][0]["id"], "title": "下單功能", "init": init}
    return client.post(f"/api/projects/{project['project']['id']}/specs", json=body).json()


def _sse_events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_run_creates_new_version(client, project):
    spec = _spec(client, project)
    r = client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "客戶可以線上下單"})
    assert r.status_code == 201, r.text
    run = wait_run(client, r.json()["id"])
    assert run["status"] == "succeeded", run["error"]
    assert run["result_version_no"] == 1
    assert run["options"]["uml_syntax"] == "auto"
    assert run["plan"]["sections"][0]["id"] == "S1"
    assert run["review"]["final"]["verdict"] == "pass"
    assert len(run["review"]["rounds"]) == 2
    assert set(run["stats"]) == {"planner", "writer", "reviewer"}

    detail = client.get(f"/api/specs/{spec['id']}").json()
    assert detail["head"]["source"] == "agent"
    assert detail["head"]["run_id"] == run["id"]
    assert detail["active_run_id"] is None
    assert "待確認事項" in detail["content"]

    types = [e["type"] for e in run["events"]]
    assert types[0] == "run_status" and types[-1] == "run_end"
    assert [e["seq"] for e in run["events"]] == list(range(1, len(types) + 1))


def test_sse_replays_finished_run(client, project):
    spec = _spec(client, project)
    run_id = client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "需求"}).json()["id"]
    wait_run(client, run_id)
    resp = client.get(f"/api/agent/runs/{run_id}/events")
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(resp.text)
    assert events[-1]["type"] == "run_end"
    assert events[-1]["data"]["status"] == "succeeded"
    # 從中間接續
    events2 = _sse_events(client.get(f"/api/agent/runs/{run_id}/events", params={"after": 5}).text)
    assert events2[0]["seq"] == 6
    events3 = _sse_events(client.get(f"/api/agent/runs/{run_id}/events", headers={"Last-Event-ID": "7"}).text)
    assert events3[0]["seq"] == 8


def test_revise_run(client, project):
    spec = _spec(client, project, init="template")
    run_id = client.post(
        "/api/agent/runs", json={"spec_id": spec["id"], "mode": "revise", "requirement": "加入退貨流程"}
    ).json()["id"]
    run = wait_run(client, run_id)
    assert run["status"] == "succeeded", run["error"]
    assert run["result_version_no"] == 2
    diff = client.get(f"/api/specs/{spec['id']}/diff").json()
    statuses = {s["status"] for s in diff["sections"]}
    assert "modified" in statuses and "unchanged" in statuses


def test_one_active_run_per_spec_and_cancel(client, project, cfg):
    spec = _spec(client, project)
    first = client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "需求"}).json()
    second = client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "需求"})
    # 第一個可能已經跑完；沒跑完時第二個要被擋下
    if client.get(f"/api/agent/runs/{first['id']}").json()["status"] in ("queued", "running"):
        assert second.status_code == 409
    wait_run(client, first["id"])
    assert client.post(f"/api/agent/runs/{first['id']}/cancel").json() == {"cancelled": False}


def test_run_validation(client, project):
    spec = _spec(client, project)
    assert (
        client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "   "}).status_code == 400
    )
    assert (
        client.post(
            "/api/agent/runs", json={"spec_id": spec["id"], "requirement": "x", "doc_type": "nope"}
        ).status_code
        == 400
    )
    assert client.post("/api/agent/runs", json={"spec_id": 999, "requirement": "x"}).status_code == 404
    client.post(f"/api/specs/{spec['id']}/archive")
    assert client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "x"}).status_code == 400


def test_list_runs_and_templates_and_settings(client, project):
    spec = _spec(client, project)
    run_id = client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "x"}).json()["id"]
    wait_run(client, run_id)
    runs = client.get("/api/agent/runs", params={"project_id": project["project"]["id"]}).json()
    assert runs[0]["id"] == run_id and runs[0]["spec_title"] == "下單功能"
    templates = client.get("/api/agent/templates").json()
    assert templates[0]["key"] == "functional_spec"
    assert client.get("/api/agent/models").json()["provider"] == "fake"

    s = client.put("/api/settings", json={"default_model": "qwen3.6:35b-a3b", "max_revisions": 2}).json()
    assert s["default_model"] == "qwen3.6:35b-a3b" and s["max_revisions"] == 2
    assert client.get("/api/settings").json()["max_revisions"] == 2
    assert client.put("/api/settings", json={"max_revisions": 9}).status_code == 422
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["plantuml"]["mode"] == "off"
    assert client.post("/api/render/plantuml", json={"source": "@startuml\nA->B\n@enduml"}).status_code == 503


def test_uiux_advice_run_never_draws(client, project):
    templates = {t["key"]: t for t in client.get("/api/agent/templates").json()}
    assert templates["uiux_advice"]["advice"] is True
    assert templates["uiux_advice"]["allow_diagrams"] is False
    assert templates["functional_spec"]["allow_diagrams"] is True

    spec = _spec(client, project)
    r = client.post(
        "/api/agent/runs",
        json={
            "spec_id": spec["id"],
            "requirement": "員工要能在手機上快速請假",
            "doc_type": "uiux_advice",
            "options": {"diagrams": True},
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["options"]["diagrams"] is False  # 工作紀錄要顯示「不畫圖」
    run = wait_run(client, r.json()["id"])
    assert run["status"] == "succeeded", run["error"]
    detail = client.get(f"/api/specs/{spec['id']}").json()
    assert detail["doc_type"] == "uiux_advice"
    assert "文件類型：UI/UX 設計建議" in detail["content"]
    assert "```" not in detail["content"]


def test_diagram_kinds_for_editor(client):
    kinds = client.get("/api/agent/diagram-kinds").json()
    keys = [k["key"] for k in kinds]
    assert keys[:3] == ["flowchart", "sequence", "class"]
    usecase = next(k for k in kinds if k["key"] == "usecase")
    assert usecase["mermaid"].startswith("usecase-beta")
    assert usecase["plantuml"].startswith("@startuml")


def test_sse_fills_in_missing_run_end(client, project):
    """工作被標成失敗但沒有 run_end 事件（例如伺服器重啟），連上時要補一個完整的結束訊息。"""
    from sagent.db import session_scope
    from sagent.models import AgentRun

    spec = _spec(client, project)
    run_id = client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "x"}).json()["id"]
    wait_run(client, run_id)
    factory = client.app.state.sagent.session_factory
    with session_scope(factory) as s:
        run = s.get(AgentRun, run_id)
        run.status = "failed"
        run.error = "伺服器重新啟動，工作被中斷"
    last_seq = client.get(f"/api/agent/runs/{run_id}").json()["events"][-1]["seq"]
    events = _sse_events(client.get(f"/api/agent/runs/{run_id}/events", params={"after": last_seq}).text)
    assert events[-1]["type"] == "run_end"
    assert events[-1]["data"]["status"] == "failed"
    assert events[-1]["message"] == "伺服器重新啟動，工作被中斷"


def test_project_list_counts_active_runs(slow_client):
    pid = slow_client.post("/api/projects", json={"name": "首頁狀態"}).json()["id"]
    tree = slow_client.get(f"/api/projects/{pid}").json()
    run_ids = []
    for title in ("A", "B"):
        body = {"phase_id": tree["phases"][0]["id"], "title": title, "init": "none"}
        spec = slow_client.post(f"/api/projects/{pid}/specs", json=body).json()
        r = slow_client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "需求"})
        run_ids.append(r.json()["id"])
    wait_status(slow_client, run_ids[0], ("running",))

    def counts():
        p = next(p for p in slow_client.get("/api/projects").json() if p["id"] == pid)
        return p["running_runs"], p["queued_runs"]

    assert counts() == (1, 1)
    for run_id in run_ids:
        wait_run(slow_client, run_id)
    assert counts() == (0, 0)
