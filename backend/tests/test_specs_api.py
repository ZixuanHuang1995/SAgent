import zipfile
from io import BytesIO


def _create_spec(client, project, **kw):
    body = {"phase_id": project["phases"][0]["id"], "title": "登入功能", "doc_type": "functional_spec"}
    body.update(kw)
    resp = client.post(f"/api/projects/{project['project']['id']}/specs", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_project_created_with_default_phases(client, project):
    assert [p["name"] for p in project["phases"]] == ["需求分析", "系統設計", "詳細設計"]
    assert project["project"]["code"] == "ORD"
    resp = client.post("/api/projects", json={"name": "訂單系統"})
    assert resp.status_code == 409
    assert "已經有" in resp.json()["detail"]


def test_project_list_has_phase_spec_counts(client, project):
    phases = project["phases"]
    _create_spec(client, project)
    _create_spec(client, project, title="註冊功能")
    archived = _create_spec(client, project, title="舊功能", phase_id=phases[1]["id"])
    client.post(f"/api/specs/{archived['id']}/archive")
    [item] = client.get("/api/projects").json()
    assert item["spec_count"] == 2
    assert [(p["name"], p["spec_count"]) for p in item["phases"]] == [
        ("需求分析", 2),
        ("系統設計", 0),
        ("詳細設計", 0),
    ]


def test_phase_crud(client, project):
    pid = project["project"]["id"]
    phase = client.post(f"/api/projects/{pid}/phases", json={"name": "第二期"}).json()
    assert phase["sort_order"] == 3
    assert client.post(f"/api/projects/{pid}/phases", json={"name": "第二期"}).status_code == 409
    renamed = client.patch(f"/api/phases/{phase['id']}", json={"name": "第二期 (Phase 2)"}).json()
    assert renamed["name"] == "第二期 (Phase 2)"
    ids = [p["id"] for p in project["phases"]] + [phase["id"]]
    assert (
        client.post(f"/api/projects/{pid}/phases/reorder", json={"phase_ids": ids[::-1]}).status_code == 204
    )
    tree = client.get(f"/api/projects/{pid}").json()
    assert tree["phases"][0]["id"] == phase["id"]
    _create_spec(client, project, phase_id=phase["id"])
    assert client.delete(f"/api/phases/{phase['id']}").status_code == 409
    assert client.delete(f"/api/phases/{project['phases'][2]['id']}").status_code == 204


def test_spec_from_template_has_version_1(client, project):
    spec = _create_spec(client, project)
    assert spec["head_version_no"] == 1
    assert spec["head"]["source"] == "template"
    assert "## 1. 目的與範圍" in spec["content"]
    assert spec["content"].startswith("# 登入功能")


def test_spec_without_content_has_no_version(client, project):
    spec = _create_spec(client, project, init="none")
    assert spec["head_version_no"] == 0
    assert spec["content"] == "" and spec["head"] is None


def test_save_versions_and_conflict(client, project):
    spec = _create_spec(client, project, init="blank")
    sid = spec["id"]
    r = client.put(
        f"/api/specs/{sid}/content",
        json={"content": "# 登入\n\n第二版", "message": "補內容", "base_version_no": 1},
    )
    assert r.status_code == 200 and r.json()["created"] is True
    assert r.json()["version"]["version_no"] == 2
    # 內容一樣（只差換行符號）不產生新版本
    r = client.put(
        f"/api/specs/{sid}/content", json={"content": "# 登入\r\n\r\n第二版\n\n", "base_version_no": 2}
    )
    assert r.json()["created"] is False and r.json()["version"]["version_no"] == 2
    # 用舊版本號存檔 → 衝突
    r = client.put(f"/api/specs/{sid}/content", json={"content": "別人的修改", "base_version_no": 1})
    assert r.status_code == 409
    versions = client.get(f"/api/specs/{sid}/versions").json()
    assert [v["version_no"] for v in versions] == [2, 1]
    assert "content" not in versions[0]


def test_diff_restore_label(client, project):
    spec = _create_spec(
        client, project, init="content", content="# A\n\n## 1. 範圍\n\n舊內容\n\n## 2. 流程\n\n不變"
    )
    sid = spec["id"]
    client.put(
        f"/api/specs/{sid}/content",
        json={"content": "# A\n\n## 1. 範圍\n\n新內容\n\n## 2. 流程\n\n不變\n\n## 3. 規則\n\n新增"},
    )
    d = client.get(f"/api/specs/{sid}/diff").json()
    assert d["from_version"] == 1 and d["to_version"] == 2
    assert d["stats"] == {"added": 5, "removed": 1}
    statuses = {s["heading"]: s["status"] for s in d["sections"]}
    assert statuses == {"1. 範圍": "modified", "2. 流程": "unchanged", "3. 規則": "added"}
    first = client.get(f"/api/specs/{sid}/diff", params={"from_version": 0, "to_version": 1}).json()
    assert first["stats"]["removed"] == 0

    r = client.post(f"/api/specs/{sid}/versions/1/restore", json={}).json()
    assert r["created"] and r["version"]["version_no"] == 3 and r["version"]["source"] == "restore"
    assert client.get(f"/api/specs/{sid}").json()["content"].count("舊內容") == 1

    assert client.put(f"/api/specs/{sid}/versions/1/label", json={"label": "v1.0"}).json()["label"] == "v1.0"
    assert client.put(f"/api/specs/{sid}/versions/2/label", json={"label": "v1.0"}).status_code == 409
    assert client.put(f"/api/specs/{sid}/versions/1/label", json={"label": ""}).json()["label"] is None


def test_search_archive_and_export(client, project):
    pid = project["project"]["id"]
    a = _create_spec(client, project, title="登入功能", init="content", content="# 登入\n\n使用 OAuth 驗證")
    _create_spec(client, project, title="報表功能", init="content", content="# 報表\n\n每月 100% 產出")
    titles = lambda **p: [s["title"] for s in client.get(f"/api/projects/{pid}/specs", params=p).json()]  # noqa: E731
    assert titles(q="oauth") == ["登入功能"]
    assert titles(q="100%") == ["報表功能"]
    assert titles(q="報表") == ["報表功能"]

    assert client.post(f"/api/specs/{a['id']}/archive").json()["archived"] is True
    assert titles() == ["報表功能"]
    assert titles(archived=True) == ["登入功能"]
    assert client.put(f"/api/specs/{a['id']}/content", json={"content": "x"}).status_code == 400
    client.post(f"/api/specs/{a['id']}/unarchive")

    md = client.get(f"/api/specs/{a['id']}/export")
    assert md.status_code == 200
    assert "filename*=UTF-8''" in md.headers["content-disposition"]
    assert md.text.startswith("# 登入")

    z = client.get(f"/api/projects/{pid}/export")
    assert z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(BytesIO(z.content)).namelist()
    assert "manifest.json" in names
    assert "需求分析/登入功能.md" in names


def test_validation_errors_are_readable(client, project):
    r = client.post(f"/api/projects/{project['project']['id']}/specs", json={"title": ""})
    assert r.status_code == 422
    assert "輸入資料不正確" in r.json()["detail"]
    assert client.get("/api/specs/9999").status_code == 404


def test_delete_project(client, project):
    pid = project["project"]["id"]
    _create_spec(client, project)
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_version_line_stats(client, project):
    spec = _create_spec(client, project, init="blank")
    sid = spec["id"]
    client.put(f"/api/specs/{sid}/content", json={"content": "# 登入\n\n第一行\n第二行\n", "base_version_no": 1})
    client.put(f"/api/specs/{sid}/content", json={"content": "# 登入\n\n第一行\n改過的第二行\n第三行\n", "base_version_no": 2})
    versions = {v["version_no"]: v for v in client.get(f"/api/specs/{sid}/versions").json()}
    assert (versions[3]["lines_added"], versions[3]["lines_removed"]) == (2, 1)
    diff = client.get(f"/api/specs/{sid}/diff", params={"from_version": 2, "to_version": 3}).json()
    assert diff["stats"] == {"added": 2, "removed": 1}


def test_migration_backfills_line_stats(cfg, client, project):
    """舊資料庫沒有 lines_added 欄位：啟動時要補欄位、補算數字。"""
    from sqlalchemy import create_engine, text

    from sagent.migrations import migrate

    spec = _create_spec(client, project, init="blank")
    sid = spec["id"]
    client.put(f"/api/specs/{sid}/content", json={"content": "a\nb\n", "base_version_no": 1})
    client.put(f"/api/specs/{sid}/content", json={"content": "a\nc\nd\n", "base_version_no": 2})

    engine = create_engine(f"sqlite:///{cfg.db_path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE spec_versions DROP COLUMN lines_added"))
        conn.execute(text("ALTER TABLE spec_versions DROP COLUMN lines_removed"))
    migrate(engine)
    migrate(engine)  # 重複執行不會出錯
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT version_no, lines_added, lines_removed FROM spec_versions WHERE spec_id = :s ORDER BY version_no"),
            {"s": sid},
        ).all()
    engine.dispose()
    assert all(r[1] is not None and r[2] is not None for r in rows)
    assert tuple(rows[-1]) == (3, 2, 1)


def test_spec_source_link(client, project):
    pid = project["project"]["id"]
    idea = _create_spec(client, project, title="點餐構想", doc_type="idea_proposal")
    feature = _create_spec(client, project, title="點餐功能", source_spec_id=idea["id"], init="none")
    assert feature["source_spec_id"] == idea["id"]
    assert feature["source_spec"]["title"] == "點餐構想"
    detail = client.get(f"/api/specs/{idea['id']}").json()
    assert [d["id"] for d in detail["derived_specs"]] == [feature["id"]]
    assert detail["source_spec"] is None

    other = client.post("/api/projects", json={"name": "別的專案"}).json()
    resp = client.post(
        f"/api/projects/{other['id']}/specs",
        json={"phase_id": other["phases"][0]["id"], "title": "x", "source_spec_id": idea["id"]},
    )
    assert resp.status_code == 400
    assert client.get(f"/api/projects/{pid}/specs").status_code == 200


def test_migration_adds_source_spec_id(tmp_path):
    """舊資料庫的 specs 沒有 source_spec_id：啟動時要補上。"""
    from sqlalchemy import create_engine, text

    from sagent.migrations import migrate

    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE specs (id INTEGER PRIMARY KEY, title TEXT)"))
        conn.execute(text("CREATE TABLE spec_versions (id INTEGER PRIMARY KEY, spec_id INTEGER, version_no INTEGER, content TEXT)"))
        conn.execute(text("INSERT INTO specs (id, title) VALUES (1, '舊文件')"))
    migrate(engine)
    migrate(engine)  # 重複執行不會出錯
    with engine.begin() as conn:
        row = conn.execute(text("SELECT source_spec_id FROM specs WHERE id = 1")).one()
    engine.dispose()
    assert row[0] is None


def test_template_categories_api(client):
    cats = client.get("/api/agent/template-categories").json()
    assert [c["key"] for c in cats] == ["idea", "feature", "design", "change"]
    templates = {t["key"]: t for t in client.get("/api/agent/templates").json()}
    assert templates["idea_proposal"]["category"] == "idea"
    assert templates["idea_proposal"]["style"] == "idea"
