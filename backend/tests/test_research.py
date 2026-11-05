"""研究計劃 C-05～C-19 的 API 和執行器測試（C-01、C-03、C-04 在 test_eval_checks.py）。"""

from __future__ import annotations

from conftest import wait_run


def _spec(client, project, doc_type="functional_spec", title="下單功能"):
    return client.post(
        f"/api/projects/{project['project']['id']}/specs",
        json={"title": title, "doc_type": doc_type, "phase_id": project["phases"][0]["id"]},
    ).json()


def _run(client, spec_id, requirement="客戶可以下單。", **options):
    body = {"spec_id": spec_id, "requirement": requirement}
    if options:
        body["options"] = options
    resp = client.post("/api/agent/runs", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --- C-05 記下 Ollama 版本和模型 digest ---


def test_fake_model_run_records_empty_environment(client, project):
    run = wait_run(client, _run(client, _spec(client, project)["id"]))
    assert run["status"] == "succeeded"
    assert run["options"]["environment"] == {}


# --- C-06 結構化的停止原因、整個工作的時間上限 ---


def test_pipeline_stop_reasons():
    import asyncio

    from test_pipeline import make_input, run

    from sagent.agents.base import ListEmitter
    from sagent.agents.fake_llm import FakeLLM
    from sagent.agents.pipeline import SpecPipeline

    # 假模型第一次審查有一個錯誤、第二次沒有
    assert run(FakeLLM(), make_input(max_revisions=2))[0].stop_reason == "passed"
    assert run(FakeLLM(), make_input(max_revisions=0))[0].stop_reason == "revision_limit"

    pipeline = SpecPipeline(FakeLLM(), ListEmitter())

    async def nothing(*_args, **_kwargs):
        return set()

    pipeline.writer.revise = nothing
    assert asyncio.run(pipeline.run(make_input(max_revisions=2))).stop_reason == "nothing_to_revise"


def test_succeeded_run_records_stop_reason_and_wall_seconds(client, project):
    run = wait_run(client, _run(client, _spec(client, project)["id"]))
    assert run["status"] == "succeeded"
    assert run["stop_reason"] == "passed"
    assert run["wall_seconds"] is not None and run["wall_seconds"] >= 0
    assert run["options"]["max_run_minutes"] == 0


def test_run_fails_when_over_time_limit(slow_client):
    tree = slow_client.get(f"/api/projects/{slow_client.post('/api/projects', json={'name': '時間上限'}).json()['id']}").json()
    assert slow_client.put("/api/settings", json={"max_run_minutes": 0.003}).status_code == 200  # 0.18 秒
    run = wait_run(slow_client, _run(slow_client, _spec(slow_client, tree)["id"]))
    assert run["status"] == "failed"
    assert run["stop_reason"] == "walltime"
    assert "時間上限" in run["error"]
    assert run["wall_seconds"] >= 0.15
    assert run["events"][-1]["type"] == "run_end"


def test_cancelled_run_records_stop_reason(slow_client):
    import time

    from conftest import wait_status

    tree = slow_client.get(f"/api/projects/{slow_client.post('/api/projects', json={'name': '取消'}).json()['id']}").json()
    run_id = _run(slow_client, _spec(slow_client, tree)["id"])
    wait_status(slow_client, run_id, ("running",))
    time.sleep(0.3)
    slow_client.post(f"/api/agent/runs/{run_id}/cancel")
    run = wait_run(slow_client, run_id)
    assert run["status"] == "cancelled" and run["stop_reason"] == "cancelled"
    assert run["wall_seconds"] > 0


def test_plan_first_run_keeps_counting_wall_seconds(client, project):
    from conftest import wait_status

    spec = _spec(client, project)
    run_id = _run(client, spec["id"], plan_first=True)
    planned = wait_status(client, run_id, ("planned",))
    assert planned["wall_seconds"] is not None and planned["stop_reason"] is None
    client.post(f"/api/agent/runs/{run_id}/continue", json={"answers": []})
    run = wait_run(client, run_id)
    assert run["status"] == "succeeded"
    assert run["wall_seconds"] >= planned["wall_seconds"]


def test_migration_adds_stop_reason_columns(cfg, client, project):
    from sqlalchemy import create_engine, text

    from sagent.migrations import migrate

    wait_run(client, _run(client, _spec(client, project)["id"]))
    engine = create_engine(f"sqlite:///{cfg.db_path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN stop_reason"))
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN wall_seconds"))
    migrate(engine)
    migrate(engine)  # 重複執行不會出錯
    with engine.begin() as conn:
        row = conn.execute(text("SELECT stop_reason, wall_seconds FROM agent_runs")).one()
    engine.dispose()
    assert tuple(row) == (None, None)  # 舊紀錄是 NULL


# --- C-08 可選的 seed ---


def test_seed_setting_goes_to_run_and_can_be_cleared(client, project):
    assert client.get("/api/settings").json()["seed"] is None
    assert client.put("/api/settings", json={"seed": 42}).json()["seed"] == 42
    run = wait_run(client, _run(client, _spec(client, project)["id"]))
    assert run["options"]["seed"] == 42
    # 其他欄位送 null 是不改，seed 送 null 是清掉
    resp = client.put("/api/settings", json={"seed": None, "temperature": None})
    assert resp.json()["seed"] is None and resp.json()["temperature"] == 0.4
    bad = client.put("/api/settings", json={"seed": -1})
    assert bad.status_code == 422 and "亂數種子" in bad.json()["detail"]


def test_default_llm_factory_passes_seed(cfg):
    from sagent.app_state import AppState

    state = AppState.__new__(AppState)
    state.cfg = cfg
    cfg.llm_provider = "ollama"
    llm = state.default_llm_factory("qwen3:8b", {"seed": 7, "ollama_url": "http://127.0.0.1:1"})
    assert llm.seed == 7
    assert state.default_llm_factory("qwen3:8b", {}).seed is None


# --- C-09 可選擇存提示詞 ---


def _step_ends(emitter):
    return [e["data"] for e in emitter.events if e["type"] == "step_end"]


def test_prompt_hash_always_and_messages_only_when_enabled():
    from test_pipeline import make_input, run

    from sagent.agents.fake_llm import FakeLLM

    _, plain = run(FakeLLM(), make_input(max_revisions=0))
    ends = _step_ends(plain)
    assert ends and all(len(d["prompt_hash"]) == 64 for d in ends)
    assert not any("messages" in d for d in ends)
    # 同樣的輸入，提示詞的雜湊一樣
    _, again = run(FakeLLM(), make_input(max_revisions=0))
    assert [d["prompt_hash"] for d in _step_ends(again)] == [d["prompt_hash"] for d in ends]

    llm = FakeLLM()
    _, stored = run(llm, make_input(max_revisions=0, store_prompts=True))
    plan_end = next(d for d in _step_ends(stored) if d["step"] == "plan")
    first_call = next(c for c in llm.calls if c["purpose"] == "planner:plan")
    assert plan_end["messages"] == first_call["messages"]


def test_store_prompts_setting_reaches_events(client, project):
    client.put("/api/settings", json={"store_prompts": True})
    run = wait_run(client, _run(client, _spec(client, project)["id"]))
    assert run["options"]["store_prompts"] is True
    ends = [e["data"] for e in run["events"] if e["type"] == "step_end"]
    assert all(d.get("messages") for d in ends if d.get("ok"))
