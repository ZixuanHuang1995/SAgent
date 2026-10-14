import threading
import time

from conftest import wait_run, wait_status


def _project(client):
    pid = client.post("/api/projects", json={"name": "取消測試"}).json()["id"]
    return client.get(f"/api/projects/{pid}").json()


def _spec(client, tree, title):
    body = {"phase_id": tree["phases"][0]["id"], "title": title, "init": "none"}
    return client.post(f"/api/projects/{tree['project']['id']}/specs", json=body).json()


def _start(client, spec_id):
    r = client.post("/api/agent/runs", json={"spec_id": spec_id, "requirement": "需求"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _event_types(client, run_id):
    return [e["type"] for e in client.get(f"/api/agent/runs/{run_id}").json()["events"]]


def test_cancel_running_run(slow_client):
    tree = _project(slow_client)
    spec = _spec(slow_client, tree, "A")
    run_id = _start(slow_client, spec["id"])
    wait_status(slow_client, run_id, ("running",))
    time.sleep(0.3)
    assert slow_client.post(f"/api/agent/runs/{run_id}/cancel").json() == {"cancelled": True}
    run = wait_run(slow_client, run_id)
    assert run["status"] == "cancelled"
    assert _event_types(slow_client, run_id)[-1] == "run_end"
    assert slow_client.get(f"/api/specs/{spec['id']}").json()["head_version_no"] == 0
    # 取消後可以馬上再開新的工作
    assert (
        slow_client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "x"}).status_code
        == 201
    )


def test_cancel_queued_run(slow_client):
    tree = _project(slow_client)
    first = _start(slow_client, _spec(slow_client, tree, "A")["id"])
    second = _start(slow_client, _spec(slow_client, tree, "B")["id"])
    third = _start(slow_client, _spec(slow_client, tree, "C")["id"])
    assert slow_client.get(f"/api/agent/runs/{third}").json()["queue_position"] == 2
    assert slow_client.post(f"/api/agent/runs/{second}/cancel").json() == {"cancelled": True}
    assert slow_client.get(f"/api/agent/runs/{second}").json()["status"] == "cancelled"
    # 拿掉之後，第三個往前排
    assert slow_client.get(f"/api/agent/runs/{third}").json()["queue_position"] == 1
    assert _event_types(slow_client, second) == ["run_end"]
    assert wait_run(slow_client, first)["status"] == "succeeded"
    assert wait_run(slow_client, third)["status"] == "succeeded"
    assert slow_client.get(f"/api/agent/runs/{second}").json()["status"] == "cancelled"


def test_cancel_while_loading_inputs(slow_client):
    """取消剛好發生在讀取資料（資料庫交易）的時候，最後還是要變成已取消。"""
    runner = slow_client.app.state.sagent.runner
    original = runner._load_input
    entered = threading.Event()

    def slow_load(run_id):
        entered.set()
        time.sleep(0.5)
        return original(run_id)

    runner._load_input = slow_load
    tree = _project(slow_client)
    spec = _spec(slow_client, tree, "A")
    run_id = _start(slow_client, spec["id"])
    assert entered.wait(5)
    assert slow_client.post(f"/api/agent/runs/{run_id}/cancel").json() == {"cancelled": True}
    run = wait_status(slow_client, run_id, ("cancelled", "succeeded", "failed"))
    assert run["status"] == "cancelled"
    assert _event_types(slow_client, run_id)[-1] == "run_end"
    assert slow_client.get(f"/api/specs/{spec['id']}").json()["active_run_id"] is None
