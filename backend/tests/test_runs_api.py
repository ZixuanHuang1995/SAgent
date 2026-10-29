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

    s = client.put("/api/settings", json={"default_model": "qwen3:8b", "max_revisions": 2}).json()
    assert s["default_model"] == "qwen3:8b" and s["max_revisions"] == 2
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
    assert keys[:3] == ["flowchart", "swimlane", "sequence"]
    usecase = next(k for k in kinds if k["key"] == "usecase")
    assert usecase["name"] == "使用案例圖" and usecase["hint"] and usecase["group"] == "流程與互動"
    assert usecase["mermaid"].startswith("usecase-beta")
    assert usecase["plantuml"].startswith("@startuml")
    # PlantUML 沒有的圖是空字串
    assert next(k for k in kinds if k["key"] == "journey")["plantuml"] == ""


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
    run_ids, spec_ids = [], []
    for title in ("A", "B"):
        body = {"phase_id": tree["phases"][0]["id"], "title": title, "init": "none"}
        spec = slow_client.post(f"/api/projects/{pid}/specs", json=body).json()
        r = slow_client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "需求"})
        run_ids.append(r.json()["id"])
        spec_ids.append(spec["id"])
    wait_status(slow_client, run_ids[0], ("running",))

    def counts():
        p = next(p for p in slow_client.get("/api/projects").json() if p["id"] == pid)
        return p["running_runs"], p["queued_runs"]

    def ai_status():
        """側邊欄和 Spec 頁上的 AI 狀態標籤"""
        specs = {s["id"]: s["ai_status"] for s in slow_client.get(f"/api/projects/{pid}").json()["specs"]}
        detail = [slow_client.get(f"/api/specs/{i}").json()["ai_status"] for i in spec_ids]
        assert detail == [specs[i] for i in spec_ids]
        return detail

    assert counts() == (1, 1)
    assert ai_status() == ["running", "queued"]
    for run_id in run_ids:
        wait_run(slow_client, run_id)
    assert counts() == (0, 0)
    assert ai_status() == [None, None]


def _wait_paused(client, run_id):
    run = wait_status(client, run_id, ("planned", "failed"))
    for _ in range(100):  # 狀態先改、run_paused 事件後送
        if run["events"] and run["events"][-1]["type"] == "run_paused":
            return run
        run = client.get(f"/api/agent/runs/{run_id}").json()
    raise AssertionError("沒有收到 run_paused")


def test_plan_first_pauses_then_continues_with_answers(client, project):
    spec = _spec(client, project)
    body = {"spec_id": spec["id"], "requirement": "客戶可以線上下單", "options": {"plan_first": True}}
    run_id = client.post("/api/agent/runs", json=body).json()["id"]
    run = _wait_paused(client, run_id)
    assert run["status"] == "planned" and run["result_version_no"] is None
    assert run["plan"]["terms"][0]["name"] == "客戶"
    assert run["stats"]["planner"]["calls"] == 1 and run["stats"]["writer"]["calls"] == 0
    # 等待確認時不能再開新的工作；SSE 送完歷史就結束，最後一個是 run_paused
    assert client.post("/api/agent/runs", json=body).status_code == 409
    assert _sse_events(client.get(f"/api/agent/runs/{run_id}/events").text)[-1]["type"] == "run_paused"

    q1, q2 = run["plan"]["open_questions"]
    first = run["plan"]["sections"][0]
    r = client.post(
        f"/api/agent/runs/{run_id}/continue",
        json={
            "answers": [{"question": q1, "answer": "約 200 人"}, {"question": q2, "answer": " "}],
            "terms": [{"name": "會員", "kind": "角色", "note": "註冊過的客戶"}],
            "sections": [{"id": first["id"], "key_points": ["只寫網站下單", ""]}],
        },
    )
    assert r.status_code == 200, r.text
    assert client.post(f"/api/agent/runs/{run_id}/continue", json={}).status_code == 409  # 已經在排隊

    run = wait_run(client, run_id)
    assert run["status"] == "succeeded", run["error"]
    assert set(run["stats"]) == {"planner", "writer", "reviewer"}
    assert run["stats"]["planner"]["calls"] == 1  # 沒有重新規劃
    assert run["plan"]["sections"][0]["key_points"] == ["只寫網站下單"]
    assert [t["name"] for t in run["plan"]["terms"]] == ["會員"]
    content = client.get(f"/api/specs/{spec['id']}").json()["content"]
    assert f"- [x] {q1}（已確認：約 200 人）" in content
    assert f"- [ ] {q2}" in content
    # 已經繼續的工作，重播時不送當時的 run_paused，畫面才不會停在那裡
    types = [e["type"] for e in _sse_events(client.get(f"/api/agent/runs/{run_id}/events").text)]
    assert "run_paused" not in types and types[-1] == "run_end"


def test_plan_first_answers_reach_writer(client, project, fake_llms):
    spec = _spec(client, project)
    body = {"spec_id": spec["id"], "requirement": "客戶可以線上下單", "options": {"plan_first": True}}
    run_id = client.post("/api/agent/runs", json=body).json()["id"]
    run = _wait_paused(client, run_id)
    q = run["plan"]["open_questions"][0]
    client.post(f"/api/agent/runs/{run_id}/continue", json={"answers": [{"question": q, "answer": "約 200 人"}]})
    wait_run(client, run_id)
    prompt = next(c for c in fake_llms[-1].calls if ":section:" in c["purpose"])["messages"][-1]["content"]
    assert "## 待確認問題的回答" in prompt and "答：約 200 人" in prompt
    # 每一節的提示另外列出相關的回答，模型才不會只當成背景資料
    prompts = [c["messages"][-1]["content"] for c in fake_llms[-1].calls if ":section:" in c["purpose"]]
    assert any("使用者確認過的回答" in p and "答：約 200 人" in p.split("## 現在要寫的章節")[1] for p in prompts)
    assert "## 名詞表" in prompt and "- 客戶（角色）：在網站下單的人" in prompt


def test_cancel_planned_run(client, project):
    spec = _spec(client, project)
    body = {"spec_id": spec["id"], "requirement": "需求", "options": {"plan_first": True}}
    run_id = client.post("/api/agent/runs", json=body).json()["id"]
    _wait_paused(client, run_id)
    assert client.post(f"/api/agent/runs/{run_id}/cancel").json() == {"cancelled": True}
    assert client.get(f"/api/agent/runs/{run_id}").json()["status"] == "cancelled"
    assert client.get(f"/api/specs/{spec['id']}").json()["active_run_id"] is None


def test_plan_first_ignored_in_revise_mode(client, project):
    spec = _spec(client, project, init="template")
    body = {"spec_id": spec["id"], "mode": "revise", "requirement": "加退貨", "options": {"plan_first": True}}
    run_id = client.post("/api/agent/runs", json=body).json()["id"]
    run = wait_run(client, run_id)
    assert run["status"] == "succeeded" and run["options"]["plan_first"] is False


def test_project_glossary_reaches_prompts(client, project, fake_llms):
    pid = project["project"]["id"]
    client.patch(f"/api/projects/{pid}", json={"glossary": [{"name": "報修人", "kind": "角色", "note": "提出報修的員工"}]})
    spec = _spec(client, project)
    run = wait_run(client, client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "需求"}).json()["id"])
    # 專案詞彙表放前面，Planner 新列的接在後面
    assert [t["name"] for t in run["plan"]["terms"]] == ["報修人", "客戶", "已送出"]
    calls = fake_llms[-1].calls
    planner = next(c for c in calls if c["purpose"].startswith("planner:"))["messages"][-1]["content"]
    assert "## 專案詞彙表" in planner and "- 報修人（角色）：提出報修的員工" in planner
    writer = next(c for c in calls if ":section:" in c["purpose"])["messages"][-1]["content"]
    assert "- 報修人（角色）：提出報修的員工" in writer


def test_continue_sends_confirmed_plan(client, project):
    spec = _spec(client, project)
    body = {"spec_id": spec["id"], "requirement": "需求", "options": {"plan_first": True}}
    run_id = client.post("/api/agent/runs", json=body).json()["id"]
    _wait_paused(client, run_id)
    client.post(f"/api/agent/runs/{run_id}/continue", json={"terms": [{"name": "會員", "kind": "角色", "note": ""}]})
    run = wait_run(client, run_id)
    update = next(e for e in run["events"] if e["type"] == "plan_update")
    assert [t["name"] for t in update["data"]["plan"]["terms"]] == ["會員"]


def test_review_checks(client, project):
    """審查項目可以逐項標成已處理；Spec 和專案清單會顯示還沒處理的數量。"""
    from sagent.db import session_scope
    from sagent.models import AgentRun

    spec = _spec(client, project)
    run_id = client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "x"}).json()["id"]
    wait_run(client, run_id)
    with session_scope(client.app.state.sagent.session_factory) as s:
        run = s.get(AgentRun, run_id)
        final = dict(run.review["final"])
        final["issues"] = [{"section_id": "S1", "severity": "high", "problem": "編號重複", "suggestion": ""}]
        final["review_points"] = [{"section_id": None, "item": "時限", "reason": ""}] * 2
        run.review = {**run.review, "final": final}

    summary = client.get(f"/api/specs/{spec['id']}").json()["review"]
    assert summary == {"run_id": run_id, "version_no": 1, "total": 3, "open": 3}

    r = client.put(f"/api/agent/runs/{run_id}/review-checks", json={"key": "point:1", "done": True})
    assert r.json() == {"checked": ["point:1"]}
    client.put(f"/api/agent/runs/{run_id}/review-checks", json={"key": "issue:0", "done": True})
    assert client.get(f"/api/agent/runs/{run_id}").json()["review_checked"] == ["issue:0", "point:1"]
    tree = client.get(f"/api/projects/{project['project']['id']}").json()
    assert tree["specs"][0]["review"]["open"] == 1

    client.put(f"/api/agent/runs/{run_id}/review-checks", json={"key": "issue:0", "done": False})
    assert client.get(f"/api/specs/{spec['id']}").json()["review"]["open"] == 2
    r = client.put(f"/api/agent/runs/{run_id}/review-checks", json={"key": "point:9", "done": True})
    assert r.status_code == 400

    # 新的 AI 工作完成後，改看新的審查結果
    run2 = client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "y"}).json()["id"]
    wait_run(client, run2)
    review = client.get(f"/api/specs/{spec['id']}").json()["review"]
    assert review is None or review["run_id"] == run2
