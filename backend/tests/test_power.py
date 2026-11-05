import sys

import pytest
from conftest import wait_run

from sagent.services.power import REASON, KeepAwake


class FakeApi:
    def __init__(self, fail_hold=False, fail_release=False):
        self.calls = []
        self.fail_hold, self.fail_release = fail_hold, fail_release

    def hold(self, reason):
        self.calls.append(("hold", reason))
        if self.fail_hold:
            raise OSError(5, "拒絕存取")
        return "handle"

    def release(self, handle):
        self.calls.append(("release", handle))
        if self.fail_release:
            raise OSError(6, "handle 無效")


def test_keep_awake_holds_once_and_releases_last():
    api = FakeApi()
    awake = KeepAwake(api=api)
    awake.hold()
    awake.hold()  # 重複 hold 不會再要求一次
    assert awake.active and api.calls == [("hold", REASON)]
    awake.release()
    assert awake.active  # 還有一個沒放開
    awake.release()
    assert not awake.active and api.calls[-1] == ("release", "handle")
    awake.release()  # 多放一次也沒關係
    assert len(api.calls) == 2


def test_keep_awake_disabled_or_failing_never_raises():
    api = FakeApi()
    with KeepAwake(enabled=False, api=api) as awake:
        assert not awake.active
    assert api.calls == []
    # 要求失敗：只記 log，AI 工作照常
    with KeepAwake(api=FakeApi(fail_hold=True)) as awake:
        assert not awake.active
    awake = KeepAwake(api=FakeApi(fail_release=True))
    awake.hold()
    awake.release()
    assert not awake.active


def test_run_keeps_computer_awake_while_running(client, project):
    api = FakeApi()
    client.app.state.sagent.runner.keep_awake = KeepAwake(api=api)
    body = {"phase_id": project["phases"][0]["id"], "title": "下單", "init": "none"}
    spec = client.post(f"/api/projects/{project['project']['id']}/specs", json=body).json()
    run = wait_run(
        client, client.post("/api/agent/runs", json={"spec_id": spec["id"], "requirement": "x"}).json()["id"]
    )
    assert run["status"] == "succeeded"
    # 開始時要求、結束時放開
    assert [c[0] for c in api.calls] == ["hold", "release"]
    assert not client.app.state.sagent.runner.keep_awake.active
    messages = [e["message"] for e in run["events"] if e["type"] == "log"]
    assert any("不會自動待命" in m for m in messages)


def test_keep_awake_off_by_env(monkeypatch, tmp_path):
    from sagent.config import load_config

    monkeypatch.setenv("SAGENT_KEEP_AWAKE", "0")
    assert load_config(data_dir=tmp_path).keep_awake is False
    monkeypatch.setenv("SAGENT_KEEP_AWAKE", "1")
    assert load_config(data_dir=tmp_path).keep_awake is True


@pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 有 power request")
def test_windows_power_request_really_applies():
    """用真的 API：要求期間系統的執行狀態有「系統」和「螢幕」兩個旗標，放開後自己的要求消失。"""
    import ctypes

    def state():
        out = ctypes.c_ulong()
        assert ctypes.WinDLL("powrprof").CallNtPowerInformation(16, None, 0, ctypes.byref(out), 4) == 0
        return out.value

    with KeepAwake() as awake:
        assert awake.active
        assert state() & 0x3 == 0x3  # ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    assert not awake.active
