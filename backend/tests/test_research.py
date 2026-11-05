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
