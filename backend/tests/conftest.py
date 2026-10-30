from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sagent.agents.fake_llm import FakeLLM
from sagent.config import ROOT_DIR, load_config
from sagent.main import create_app

# 從內建移除的範本備份在這裡，放進自訂範本照樣能用
BACKUP_TEMPLATES = ROOT_DIR / "backup" / "doc_templates"


def install_backup_template(cfg, key: str) -> None:
    """把備份的範本放進自訂範本資料夾，不用重新啟動就會生效"""
    cfg.templates_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(BACKUP_TEMPLATES / f"{key}.yaml", cfg.templates_dir)


@pytest.fixture
def cfg(tmp_path: Path):
    return load_config(
        data_dir=tmp_path,
        db_path=tmp_path / "test.db",
        llm_provider="fake",
        fake_delay=0.0,
        frontend_dist=tmp_path / "no-dist",
        plantuml_server="",
        plantuml_jar=tmp_path / "no.jar",
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )


@pytest.fixture
def fake_llms():
    """記錄每次建立的 FakeLLM，測試可以檢查呼叫內容。"""
    return []


@pytest.fixture
def client(cfg, fake_llms):
    def factory(model, opts):
        llm = FakeLLM(model=model)
        fake_llms.append(llm)
        return llm

    app = create_app(cfg, llm_factory=factory)
    with TestClient(app, headers={"X-SAgent-Client": "test"}) as c:
        yield c


@pytest.fixture
def project(client):
    resp = client.post(
        "/api/projects", json={"name": "訂單系統", "code": "ORD", "description": "線上訂單管理"}
    )
    assert resp.status_code == 201, resp.text
    tree = client.get(f"/api/projects/{resp.json()['id']}").json()
    return tree


def wait_run(client: TestClient, run_id: int, timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/agent/runs/{run_id}").json()
        # runner 先改狀態、再送 run_end 事件，兩個都到了才算結束，不然偶爾會拿到少了 run_end 的事件
        ended = not run["events"] or run["events"][-1]["type"] == "run_end"
        if run["status"] in ("succeeded", "failed", "cancelled") and ended:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} 沒有在時間內完成")


@pytest.fixture
def slow_client(cfg):
    """假模型每次呼叫先停 0.2 秒，一次 AI 工作大約 3～4 秒，用來測試取消和排隊。"""
    app = create_app(cfg, llm_factory=lambda model, opts: FakeLLM(model=model, call_delay=0.2))
    with TestClient(app, headers={"X-SAgent-Client": "test"}) as c:
        yield c


def wait_status(client: TestClient, run_id: int, statuses: tuple[str, ...], timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/agent/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} 沒有進入 {statuses}")
