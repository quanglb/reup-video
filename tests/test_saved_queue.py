"""Hàng chờ: lưu video từ tab quét, làm dần, bỏ khỏi hàng."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reup.core.store import Store
from reup.web.app import create_app

from tests.test_web_app import FakeRunner


def video(vid: str, platform: str = "youtube", **extra) -> dict:
    return {
        "platform": platform, "video_id": vid, "url": f"https://www.youtube.com/shorts/{vid}",
        "embed_url": f"https://www.youtube.com/embed/{vid}", "title": f"Video {vid}",
        "thumbnail": "https://i.ytimg.com/x.jpg", "duration_ms": 30_000,
        "view_count": 2_500_000, "like_count": 9_000, **extra,
    }


def test_store_keeps_queue_order_and_updates_data(tmp_path: Path):
    s = Store(tmp_path / "db")
    s.init_schema()
    s.save_video("youtube", "a", {"title": "A"})
    s.save_video("douyin", "b", {"title": "B"})
    s.save_video("youtube", "a", {"title": "A mới"})
    assert [(v["video_id"], v["title"]) for v in s.saved_videos()] == [("a", "A mới"), ("b", "B")]
    assert [v["video_id"] for v in s.saved_videos("douyin")] == ["b"]
    s.unsave_video("youtube", "a")
    assert s.saved_keys() == {("douyin", "b")}
    s.close()


@pytest.fixture
def client(tmp_path: Path, config_file: Path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    return TestClient(app), db


def test_saving_shows_it_in_the_queue_page(client):
    c, _ = client
    r = c.post("/saved", json={"video": video("abc"), "saved": True})
    assert r.json() == {"saved": True, "count": 1}

    body = c.get("/saved").text
    assert "Video abc" in body and "2.5M" in body
    assert 'id="savedCount">1<' in body

    assert c.post("/saved", json={"video": video("abc"), "saved": False}).json()["count"] == 0
    assert "Hàng chờ trống" in c.get("/saved").text


def test_saving_rejects_a_bad_link(client):
    c, _ = client
    assert c.post("/saved", json={"video": video("x", url="javascript:alert(1)")}).status_code == 400


def test_running_from_the_queue_creates_a_job_and_leaves_the_queue(client):
    c, db = client
    c.post("/saved", json={"video": video("a")})
    c.post("/saved", json={"video": video("b")})

    r = c.post("/saved/run", data={"platform": "youtube", "video_id": "b"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    s = Store(db)
    assert [v["video_id"] for v in s.saved_videos()] == ["a"]
    assert s.is_seen("youtube", "b")
    assert s.list_jobs()[0]["url"] == "https://www.youtube.com/shorts/b"
    s.close()
    assert len(c.app.state.runner.started) == 1


def test_next_takes_the_oldest_saved(client):
    c, db = client
    c.post("/saved", json={"video": video("first")})
    c.post("/saved", json={"video": video("second")})
    r = c.post("/saved/next", follow_redirects=False)
    assert r.headers["location"] == "/saved"
    s = Store(db)
    assert [v["video_id"] for v in s.saved_videos()] == ["second"]
    s.close()


def test_remove_and_empty_next(client):
    c, _ = client
    c.post("/saved", json={"video": video("a")})
    c.post("/saved/remove", data={"platform": "youtube", "video_id": "a"})
    assert c.post("/saved/next").status_code == 404


def test_batch_runs_the_ticked_videos_in_queue_order(client):
    c, db = client
    for vid in ("a", "b", "c"):
        c.post("/saved", json={"video": video(vid)})

    r = c.post(
        "/saved/run-batch",
        data={"keys": ["youtube:c", "youtube:a"]},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/?batch=2"
    assert len(c.app.state.runner.started) == 2
    s = Store(db)
    assert [v["video_id"] for v in s.saved_videos()] == ["b"]
    assert sorted(j["url"][-1] for j in s.list_jobs()) == ["a", "c"]
    s.close()


def test_batch_first_n_takes_the_head_of_the_queue(client):
    c, db = client
    for vid in ("a", "b", "c"):
        c.post("/saved", json={"video": video(vid)})
    c.post("/saved/run-batch", data={"first": 2})
    s = Store(db)
    assert [v["video_id"] for v in s.saved_videos()] == ["c"]
    s.close()


def test_batch_with_nothing_picked_is_refused(client):
    c, _ = client
    c.post("/saved", json={"video": video("a")})
    assert c.post("/saved/run-batch", data={}).status_code == 400
