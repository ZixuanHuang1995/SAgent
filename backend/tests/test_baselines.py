import json
import zipfile
from io import BytesIO


def _spec(client, project, title, content, phase_index=0):
    body = {
        "phase_id": project["phases"][phase_index]["id"],
        "title": title,
        "init": "content",
        "content": content,
    }
    return client.post(f"/api/projects/{project['project']['id']}/specs", json=body).json()


def test_baseline_snapshot_compare_and_export(client, project):
    pid = project["project"]["id"]
    a = _spec(client, project, "登入功能", "# 登入\n\nv1")
    b = _spec(client, project, "報表功能", "# 報表\n\nv1", phase_index=1)
    _spec(client, project, "空白文件", "")  # 沒有版本，不會納入

    base = client.post(f"/api/projects/{pid}/baselines", json={"name": "需求凍結 v1.0", "note": "客戶確認"})
    assert base.status_code == 201, base.text
    base = base.json()
    assert base["item_count"] == 2
    assert client.post(f"/api/projects/{pid}/baselines", json={"name": "需求凍結 v1.0"}).status_code == 409

    detail = client.get(f"/api/baselines/{base['id']}").json()
    assert {(i["title"], i["version_no"]) for i in detail["items"]} == {("登入功能", 1), ("報表功能", 1)}

    # 之後的變更：改登入、封存報表、新增一份
    client.put(f"/api/specs/{a['id']}/content", json={"content": "# 登入\n\nv2"})
    client.post(f"/api/specs/{b['id']}/archive")
    _spec(client, project, "權限功能", "# 權限\n\nv1")

    cmp = client.get(f"/api/projects/{pid}/compare", params={"from_baseline": base["id"]}).json()
    status = {i["title"]: i["status"] for i in cmp["items"]}
    assert status == {"登入功能": "changed", "報表功能": "removed", "權限功能": "added"}

    # 還原成跟基準版本一樣的內容 → 視為未變更
    client.post(f"/api/specs/{a['id']}/versions/1/restore", json={})
    cmp = client.get(f"/api/projects/{pid}/compare", params={"from_baseline": base["id"]}).json()
    assert {i["title"]: i["status"] for i in cmp["items"]}["登入功能"] == "unchanged"

    # 匯出基準版本：內容是當時的版本
    z = client.get(f"/api/projects/{pid}/export", params={"baseline_id": base["id"]})
    zf = zipfile.ZipFile(BytesIO(z.content))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["baseline"] == "需求凍結 v1.0"
    assert sorted(s["title"] for s in manifest["specs"]) == ["報表功能", "登入功能"]
    assert zf.read("需求萃取/登入功能.md").decode().endswith("v1\n")

    assert client.delete(f"/api/baselines/{base['id']}").status_code == 204
    assert client.get(f"/api/projects/{pid}/baselines").json() == []


def test_baseline_apply_label(client, project):
    pid = project["project"]["id"]
    a = _spec(client, project, "登入功能", "# 登入\n\nv1")
    b = _spec(client, project, "報表功能", "# 報表\n\nv1")
    c = _spec(client, project, "權限功能", "# 權限\n\nv1")
    # b 的最新版已經有別的標籤：不蓋掉
    client.put(f"/api/specs/{b['id']}/versions/1/label", json={"label": "草稿"})
    # c 的舊版已經用了同名標籤：不從舊版搬過來
    client.put(f"/api/specs/{c['id']}/versions/1/label", json={"label": "需求凍結"})
    client.put(f"/api/specs/{c['id']}/content", json={"content": "# 權限\n\nv2"})

    r = client.post(f"/api/projects/{pid}/baselines", json={"name": "需求凍結", "apply_label": True})
    assert r.status_code == 201, r.text
    assert r.json()["labeled_count"] == 1
    def labels(spec):
        return {v["version_no"]: v["label"] for v in client.get(f"/api/specs/{spec['id']}/versions").json()}

    assert labels(a) == {1: "需求凍結"}
    assert labels(b) == {1: "草稿"}
    assert labels(c) == {1: "需求凍結", 2: None}

    # 沒有勾選時不加標籤，也不回 labeled_count
    r = client.post(f"/api/projects/{pid}/baselines", json={"name": "第二次"})
    assert r.json()["labeled_count"] is None
    assert all(v["label"] != "第二次" for v in client.get(f"/api/specs/{a['id']}/versions").json())


def test_baseline_requires_versions(client, project):
    pid = project["project"]["id"]
    r = client.post(f"/api/projects/{pid}/baselines", json={"name": "空的"})
    assert r.status_code == 400


def test_export_includes_diagrams(client, project):
    pid = project["project"]["id"]
    _spec(
        client,
        project,
        "流程",
        "# 流程\n\n```mermaid\nflowchart TD\nA --> B\n```\n\n```plantuml\n@startuml\nA -> B\n@enduml\n```",
    )
    zf = zipfile.ZipFile(BytesIO(client.get(f"/api/projects/{pid}/export").content))
    names = zf.namelist()
    assert "diagrams/需求萃取/流程-1.mmd" in names
    assert "diagrams/需求萃取/流程-2.puml" in names


def test_phase_baseline_compares_only_its_phase(client, project):
    pid = project["project"]["id"]
    phase1 = project["phases"][0]["id"]
    _spec(client, project, "需求一", "# 1")
    _spec(client, project, "設計一", "# 2", phase_index=1)
    base = client.post(f"/api/projects/{pid}/baselines", json={"name": "需求階段", "phase_id": phase1}).json()
    assert base["item_count"] == 1
    items = client.get(f"/api/projects/{pid}/compare", params={"from_baseline": base["id"]}).json()["items"]
    assert [(i["title"], i["status"]) for i in items] == [("需求一", "unchanged")]


def test_zip_names_never_collide(client, project):
    pid = project["project"]["id"]
    a = _spec(client, project, "報表", "# A")
    _spec(client, project, f"報表-{a['id'] + 2}", "# B")  # 跟第三份改名後的檔名一樣
    _spec(client, project, "報表.", "# C")  # safe_filename 後也是「報表」
    zf = zipfile.ZipFile(BytesIO(client.get(f"/api/projects/{pid}/export").content))
    names = [n for n in zf.namelist() if n.endswith(".md")]
    assert len(names) == len(set(names)) == 3
