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


# --- C-10 文件層級的人工判斷 ---


def test_set_and_clear_verdict(client, project):
    run_id = wait_run(client, _run(client, _spec(client, project)["id"]))["id"]
    resp = client.put(f"/api/agent/runs/{run_id}/verdict", json={"verdict": "partial", "note": " 狀態漏了退回 "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["human_verdict"] == "partial"
    assert resp.json()["human_verdict_note"] == "狀態漏了退回"
    assert client.get(f"/api/agent/runs/{run_id}").json()["human_verdict"] == "partial"
    cleared = client.put(f"/api/agent/runs/{run_id}/verdict", json={"verdict": None, "note": "x"}).json()
    assert cleared["human_verdict"] is None and cleared["human_verdict_note"] == ""
    bad = client.put(f"/api/agent/runs/{run_id}/verdict", json={"verdict": "maybe"})
    assert bad.status_code == 422 and "能不能交付" in bad.json()["detail"]
    assert client.put("/api/agent/runs/9999/verdict", json={"verdict": "yes"}).status_code == 404


def test_verdict_only_for_succeeded_runs(client, project):
    from conftest import wait_status

    run_id = _run(client, _spec(client, project)["id"], plan_first=True)
    wait_status(client, run_id, ("planned",))
    resp = client.put(f"/api/agent/runs/{run_id}/verdict", json={"verdict": "yes"})
    assert resp.status_code == 409


def test_migration_adds_verdict_columns(cfg, client, project):
    from sqlalchemy import create_engine, text

    from sagent.migrations import migrate

    wait_run(client, _run(client, _spec(client, project)["id"]))
    engine = create_engine(f"sqlite:///{cfg.db_path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN human_verdict"))
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN human_verdict_note"))
    migrate(engine)
    migrate(engine)
    with engine.begin() as conn:
        row = conn.execute(text("SELECT human_verdict, human_verdict_note FROM agent_runs")).one()
    engine.dispose()
    assert tuple(row) == (None, "")


# --- C-11 人工覆核項目分組 ---


def test_point_groups():
    from sagent.agents.reviewer import ITEM_GAP_REASON, NUMBER_GAP_REASON
    from sagent.services.reviews import point_group, with_groups

    assert point_group({"reason": NUMBER_GAP_REASON, "source": "check"}) == "trace"
    assert point_group({"reason": ITEM_GAP_REASON, "source": "check"}) == "trace"
    assert point_group({"reason": "需求沒提到", "source": "llm"}) == "model"
    assert point_group({"reason": "文件標註（待確認）", "source": "check"}) == "marked"
    stored = {"final": {"review_points": [{"reason": "x", "source": "llm"}]}, "rounds": []}
    shown = with_groups(stored)
    assert shown["final"]["review_points"][0]["group"] == "model"
    assert "group" not in stored["final"]["review_points"][0]  # 不改存的內容
    assert with_groups(None) is None


def test_run_detail_review_points_have_groups(client, project):
    run = wait_run(client, _run(client, _spec(client, project)["id"], requirement="客戶可以下單，訂單 30 天內可以退貨。"))
    points = run["review"]["final"]["review_points"]
    assert points and all(p["group"] in ("trace", "model", "marked") for p in points)


# --- C-13 撰寫時參考同專案的其他 Spec ---

REFERENCE_DOC = """# 下單功能

## 1. 功能需求

| 編號 | 功能 | 說明 |
|---|---|---|
| FR-01 | 送出訂單 | 客戶選好商品後送出，狀態為「待付款」 |
| FR-02 | 取消訂單 | 付款前可以取消 |

## 2. 狀態

訂單狀態依序為：待付款、已付款、已出貨。

```mermaid
flowchart TD
  A[FR-99 不算] --> B
```
"""


def _reference_spec(client, project, title="下單功能規格"):
    spec = _spec(client, project, title=title)
    resp = client.put(f"/api/specs/{spec['id']}/content", json={"content": REFERENCE_DOC, "base_version_no": spec["head_version_no"]})
    assert resp.status_code in (200, 201), resp.text
    return spec


def test_reference_specs_are_validated(client, project):
    target = _spec(client, project, title="架構設計")
    ref = _reference_spec(client, project)
    other = client.post("/api/projects", json={"name": "別的專案"}).json()
    other_tree = client.get(f"/api/projects/{other['id']}").json()
    foreign = _spec(client, other_tree, title="別的")

    def create(ids):
        return client.post(
            "/api/agent/runs",
            json={"spec_id": target["id"], "requirement": "x", "options": {"reference_spec_ids": ids}},
        )

    assert create([foreign["id"]]).status_code == 400
    assert create([target["id"]]).status_code == 400
    assert create([1, 2, 3, 4]).status_code == 422
    resp = create([ref["id"], ref["id"]])
    assert resp.status_code == 201
    assert resp.json()["options"]["reference_specs"] == [{"id": ref["id"], "title": "下單功能規格"}]
    wait_run(client, resp.json()["id"])


def test_reference_text_goes_into_planner_writer_and_reviewer(client, project, fake_llms):
    target = _spec(client, project, title="架構設計")
    ref = _reference_spec(client, project)
    run = wait_run(client, _run(client, target["id"], reference_spec_ids=[ref["id"]]))
    assert run["status"] == "succeeded"
    calls = [c for llm in fake_llms for c in llm.calls]
    by_role = {role: [c for c in calls if c["purpose"].startswith(role)] for role in ("planner", "writer", "reviewer")}
    for role, cs in by_role.items():
        assert cs, role
        user = cs[0]["messages"][-1]["content"]
        assert "（參考文件開始）" in user and "（參考文件結束）" in user, role
        assert "FR-01 送出訂單" in user and "已出貨" in user, role
        assert "FR-99" not in user  # 程式碼區塊裡的不算
    review_system = by_role["reviewer"][0]["messages"][0]["content"]
    assert "不算「需求沒提到」" in review_system


def test_no_reference_section_without_references(client, project, fake_llms):
    wait_run(client, _run(client, _spec(client, project)["id"]))
    calls = [c for llm in fake_llms for c in llm.calls]
    assert calls and not any("參考文件" in m["content"] for c in calls for m in c["messages"])


# --- C-14 第一輪審查太長時分批 ---


def test_token_estimate_uses_largest_observed_ratio():
    from sagent.agents.base import TokenEstimate

    est = TokenEstimate()
    assert est.ratio("m") == TokenEstimate.DEFAULT
    est.add("m", 1000, 800)
    est.add("m", 1000, 300)  # 命中快取時回報的 token 變少，不拿來降低估計
    est.add("m", 100, 100)  # 太短的不算
    assert est.ratio("m") == 0.8
    assert est.ratio("other") == 0.8  # 沒有這個模型的資料時用其他模型的
    assert est.tokens("m", 1000) == 801


def _review_prompt_tokens():
    """撰寫模式的第一輪審查提示詞，用假模型的算法（字元數 ÷ 2）大約幾個 token。"""
    from test_pipeline import make_input, run

    from sagent.agents.fake_llm import FakeLLM

    llm = FakeLLM()
    run(llm, make_input(max_revisions=0))
    call = next(c for c in llm.calls if c["purpose"] == "reviewer:review:1")
    return len("\n".join(m["content"] for m in call["messages"])) // 2


def _small_context_llm(limit, fail_batch=None):
    from sagent.agents.fake_llm import FakeLLM

    class SmallContext(FakeLLM):
        async def context_length(self):
            return limit

        async def chat(self, messages, **kw):
            result = await super().chat(messages, **kw)
            if fail_batch and any(f"第 {fail_batch} 批" in m["content"] for m in messages):
                result.text = "不是 JSON"
            return result

    return SmallContext()


def test_first_review_is_split_when_too_long():
    from test_pipeline import make_input, run

    whole = _review_prompt_tokens()
    limit = int((whole * 0.6 + 3000) / 0.85)  # 放得下大約六成
    llm = _small_context_llm(limit)
    result, emitter = run(llm, make_input(max_revisions=0))
    first = [c for c in llm.calls if c["purpose"] == "reviewer:review:1"]
    assert len(first) >= 2
    assert all("批，共" in c["messages"][-1]["content"] for c in first)
    logs = [e["message"] for e in emitter.events if e["type"] == "log"]
    assert any("分成" in m and "批審查" in m for m in logs)
    review = result.reviews[0]
    assert review.llm_ok and review.summary.startswith("文件太長，分成")
    # 假模型每一批都說 S2 有問題，只有包含 S2 的那一批算，不重複
    llm_issues = [i for i in review.issues if i.source == "llm"]
    assert len(llm_issues) == 1 and llm_issues[0].section_id == "S2"
    # 每一節都只在一批裡
    end = next(e for e in emitter.events if e["type"] == "step_end" and e["data"]["step"] == "review:1")
    batches = end["data"]["output"]["batches"]
    assert sorted(s for b in batches for s in b) == sorted({s.id for s in result.plan.sections})


def test_failed_batch_is_listed_for_human_review():
    from test_pipeline import make_input, run

    whole = _review_prompt_tokens()
    llm = _small_context_llm(int((whole * 0.6 + 3000) / 0.85), fail_batch=2)
    result, _ = run(llm, make_input(max_revisions=0))
    review = result.reviews[0]
    assert not review.llm_ok
    assert any("沒有審查到的章節" in p.item for p in review.review_points)


def test_review_not_split_when_context_is_enough_or_unknown():
    from test_pipeline import make_input, run

    llm = _small_context_llm(262144)
    run(llm, make_input(max_revisions=0))
    assert len([c for c in llm.calls if c["purpose"] == "reviewer:review:1"]) == 1


# --- C-19 人改過的章節當 few-shot 範例（實驗） ---


def test_load_examples(tmp_path):
    from sagent.agents.examples import SEPARATOR, example_file_name, load_examples

    assert example_file_name("4. 功能需求") == "功能需求.md"
    assert example_file_name("輸入/輸出") == "輸入_輸出.md"
    folder = tmp_path / "functional_spec"
    folder.mkdir()
    (folder / "功能需求.md").write_text(
        f"範例一\n{SEPARATOR}\n{'長' * 2000}\n{SEPARATOR}\n範例三", encoding="utf-8"
    )
    examples = load_examples(tmp_path, "functional_spec", ["4. 功能需求", "5. 業務流程"])
    assert list(examples) == ["功能需求"]
    assert examples["功能需求"][0] == "範例一"
    assert len(examples["功能需求"]) == 2 and len(examples["功能需求"][1]) == 1500
    assert load_examples(tmp_path, "architecture", ["功能需求"]) == {}
    assert load_examples(None, "functional_spec", ["功能需求"]) == {}


def test_examples_go_last_in_section_prompt():
    from test_pipeline import make_input

    from sagent.agents.prompts import section_messages
    from sagent.agents.types import Plan, SectionPlan

    inp = make_input()
    plan = Plan(title="t", summary="s", sections=[SectionPlan("S4", "功能需求", key_points=["送出訂單"])])
    plain = section_messages(inp, plan, plan.sections[0], 4, [])[-1]["content"]
    assert "範例" not in plain
    inp.examples = {"功能需求": ["| FR-01 | 範例內容 |"]}
    user = section_messages(inp, plan, plan.sections[0], 4, [])[-1]["content"]
    assert "（範例開始）\n| FR-01 | 範例內容 |\n（範例結束）" in user
    assert user.index("（範例開始）") > user.index("## 現在要寫的章節") and user.rstrip().endswith("的內文。")
    # 前段（跟其他章節共用的部分）不變，快取照樣有效
    assert user.split("## 現在要寫的章節")[0] == plain.split("## 現在要寫的章節")[0]


def test_fewshot_setting_loads_examples_from_data_dir(cfg, client, project, fake_llms):
    folder = cfg.data_dir / "examples" / "functional_spec"
    folder.mkdir(parents=True)
    (folder / "功能需求.md").write_text("| FR-01 | 人改過的範例 |", encoding="utf-8")
    spec_id = _spec(client, project)["id"]
    wait_run(client, _run(client, spec_id))  # 預設關閉：沒有範例
    assert not any("人改過的範例" in m["content"] for llm in fake_llms for c in llm.calls for m in c["messages"])
    client.put("/api/settings", json={"fewshot_examples": True})
    run = wait_run(client, _run(client, spec_id))
    assert run["options"]["fewshot_examples"] is True
    sections = [c for c in fake_llms[-1].calls if c["purpose"].startswith("writer:section:")]
    with_example = [c for c in sections if "人改過的範例" in c["messages"][-1]["content"]]
    assert len(with_example) == 1 and "第 4 節：功能需求" in with_example[0]["messages"][-1]["content"]
