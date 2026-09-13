"""Kênh Douyin đã lưu: lưu, đổi tên, bỏ lưu, và nạp file có tick lưu."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reup.core.store import Store
from reup.web import douyin_channels as dc
from reup.web.app import create_app

from tests.test_douyin_export import export, video
from tests.test_web_app import FakeRunner


def write_export(folder: Path, name: str, uid: str, author: str = "厨师") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    raw = {**export(video("a", 1, 5)), "sec_user_id": uid, "author": author}
    p = folder / name
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return p


def test_saved_channel_uses_the_newest_export_and_leaves_recent(tmp_path):
    import os

    old = write_export(tmp_path, "old.json", "u1")
    new = write_export(tmp_path, "new.json", "u1")
    os.utime(old, (1, 1))
    write_export(tmp_path, "other.json", "u2", author="阿明")

    assert {r["uid"] for r in dc.recent_unsaved(tmp_path)} == {"u1", "u2"}
    dc.save_channel(tmp_path, "u1", "  Bếp  Trung ")

    saved = dc.saved_channels(tmp_path)
    assert [(c["uid"], c["name"], c["path"]) for c in saved] == [
        ("u1", "Bếp Trung", str(new.resolve()))
    ]
    assert [r["uid"] for r in dc.recent_unsaved(tmp_path)] == ["u2"]


def test_empty_name_falls_back_to_author_and_rename_keeps_it_saved(tmp_path):
    write_export(tmp_path, "a.json", "u1", author="厨师")
    assert dc.save_channel(tmp_path, "u1")["name"] == "厨师"
    dc.save_channel(tmp_path, "u1", "Tên mới")
    assert [c["name"] for c in dc.saved_channels(tmp_path)] == ["Tên mới"]


def test_removing_keeps_the_file(tmp_path):
    f = write_export(tmp_path, "a.json", "u1")
    dc.save_channel(tmp_path, "u1")
    dc.remove_channel(tmp_path, "u1")
    assert dc.saved_channels(tmp_path) == []
    assert f.exists()
    assert [r["uid"] for r in dc.recent_unsaved(tmp_path)] == ["u1"]


def test_saving_an_unknown_channel_is_refused(tmp_path):
    with pytest.raises(ValueError):
        dc.save_channel(tmp_path, "khong-co")


@pytest.fixture
def client(tmp_path: Path, config_file: Path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    return TestClient(app), tmp_path / "douyin-exports"


def test_importing_with_save_ticked_lists_the_channel(client, monkeypatch):
    c, folder = client
    body = json.dumps({**export(video("a", 1, 5)), "sec_user_id": "u9"}, ensure_ascii=False)
    r = c.post(
        "/discover/douyin/import",
        files={"file": ("k.json", body, "application/json")},
        data={"save": "1", "name": "Kênh nấu ăn"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert [ch["name"] for ch in dc.saved_channels(folder)] == ["Kênh nấu ăn"]

    monkeypatch.setattr(
        "reup.adapters.douyin_export.search_url", lambda kw: "https://douyin/search"
    )
    page = c.get("/discover?platform=douyin").text
    assert "Kênh nấu ăn" in page

    c.post("/discover/douyin/channels/delete", data={"uid": "u9"})
    assert dc.saved_channels(folder) == []
    c.post("/discover/douyin/channels", data={"uid": "u9", "name": "Lại lưu"})
    assert [ch["name"] for ch in dc.saved_channels(folder)] == ["Lại lưu"]


def test_importing_without_save_does_not_list_it(client):
    c, folder = client
    body = json.dumps({**export(video("a", 1, 5)), "sec_user_id": "u9"}, ensure_ascii=False)
    c.post("/discover/douyin/import", files={"file": ("k.json", body, "application/json")})
    assert dc.saved_channels(folder) == []
    assert [r["uid"] for r in dc.recent_unsaved(folder)] == ["u9"]
