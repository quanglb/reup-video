"""Tab quét nguồn trong Web UI: tabs, quét theo yêu cầu, chọn video thành job."""
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reup.core.store import Store
from reup.web.app import create_app


from tests.test_web_app import FakeRunner


@pytest.fixture
def client(tmp_path: Path, config_file: Path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    return TestClient(app), jobs, db


def c_get(client, url):
    c, _, _ = client
    return c.get(url)


def fake_ytdlp(monkeypatch, stdout: str, code: int = 0, stderr: str = ""):
    real = subprocess.run

    def run(cmd, **kwargs):
        if cmd[0] != "yt-dlp":
            return real(cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", run)


def entries(*ids: str) -> str:
    return "\n".join(
        json.dumps({"id": i, "duration": 30, "title": f"Clip {i}", "view_count": 2_500_000})
        for i in ids
    )


def test_every_platform_has_a_tab(client):
    c, _, _ = client
    body = c.get("/").text
    for label in ("YouTube", "TikTok", "Douyin"):
        assert f">{label}</a>" in body


def test_opening_a_tab_does_not_scan(client, monkeypatch):
    """Mở tab phải hiện ra ngay: một lần quét mất hàng chục giây."""
    def boom(cmd, **kwargs):
        raise AssertionError("không được gọi yt-dlp khi chỉ mở tab")

    monkeypatch.setattr(subprocess, "run", boom)
    r = c_get(client, "/discover?platform=tiktok")
    assert r.status_code == 200
    assert "Bấm <b>Quét</b>" in r.text


def test_unknown_platform_is_404(client):
    assert c_get(client, "/discover?platform=instagram").status_code == 404


def test_scan_renders_a_card_with_an_embed(client, monkeypatch):
    fake_ytdlp(monkeypatch, entries("abc"))
    r = c_get(client, "/discover?platform=youtube&run=1")
    assert r.status_code == 200
    assert "https://www.youtube.com/embed/abc" in r.text
    assert "Chọn và chạy" in r.text
    assert "2.5M" in r.text  # số lượt xem rút gọn


def test_scan_failure_shows_the_crawler_message(client, monkeypatch):
    fake_ytdlp(monkeypatch, "", code=1, stderr="Unable to extract")
    r = c_get(client, "/discover?platform=tiktok&run=1")
    assert r.status_code == 200
    assert "cookies_from_browser" in r.text


def test_picking_a_video_creates_a_job_and_marks_it_seen(client):
    c, jobs, db = client
    r = c.post(
        "/discover/pick",
        data={
            "url": "https://www.youtube.com/shorts/abc",
            "lang": "zh",
            "platform": "youtube",
            "video_id": "abc",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    job_id = r.headers["location"].removeprefix("/jobs/")
    s = Store(db)
    assert s.get_job(job_id)["url"] == "https://www.youtube.com/shorts/abc"
    assert s.is_seen("youtube", "abc")
    s.close()


def test_a_video_already_turned_into_a_job_is_not_offered_twice(client, monkeypatch):
    c, _, _ = client
    c.post(
        "/discover/pick",
        data={"url": "https://www.youtube.com/shorts/abc", "platform": "youtube",
              "video_id": "abc"},
        follow_redirects=False,
    )
    fake_ytdlp(monkeypatch, entries("abc", "xyz"))
    body = c_get(client, "/discover?platform=youtube&run=1").text
    assert "Đã có job" in body
    assert body.count("Chọn và chạy") == 1


def test_view_counts_are_shortened():
    from reup.web import service

    assert service.view_label(1_568_100_000) == "1.6 tỷ"
    assert service.view_label(2_500_000) == "2.5M"
    assert service.view_label(970_800) == "971K"
    assert service.view_label(0) == ""


def test_durations_are_shown_as_minutes():
    from reup.web import service

    assert service.duration_label(75_000) == "1:15"
    assert service.duration_label(0) == "?"


def entries_varied() -> str:
    return "\n".join([
        json.dumps({"id": "slow", "duration": 170, "title": "Dài", "view_count": 10}),
        json.dumps({"id": "pop", "duration": 60, "title": "Nổi", "view_count": 9_000_000}),
        json.dumps({"id": "tiny", "duration": 8, "title": "Ngắn", "view_count": 500}),
    ])


def card_order(body: str) -> list[str]:
    import re

    return re.findall(r'name="video_id" value="([^"]+)"', body)


def test_results_keep_page_order_by_default(client, monkeypatch):
    fake_ytdlp(monkeypatch, entries_varied())
    body = c_get(client, "/discover?platform=youtube&run=1").text
    assert card_order(body) == ["slow", "pop", "tiny"]


def test_sorting_by_views_puts_the_biggest_first(client, monkeypatch):
    fake_ytdlp(monkeypatch, entries_varied())
    body = c_get(client, "/discover?platform=youtube&run=1&sort=views").text
    assert card_order(body)[0] == "pop"


def test_sorting_by_length_puts_the_shortest_first(client, monkeypatch):
    fake_ytdlp(monkeypatch, entries_varied())
    body = c_get(client, "/discover?platform=youtube&run=1&sort=short").text
    assert card_order(body) == ["tiny", "pop", "slow"]


def test_an_unknown_sort_is_reported_not_ignored(client, monkeypatch):
    fake_ytdlp(monkeypatch, entries_varied())
    body = c_get(client, "/discover?platform=youtube&run=1&sort=magic").text
    assert "không có kiểu sắp xếp" in body


def test_hide_seen_drops_videos_already_turned_into_jobs(client, monkeypatch):
    c, _, _ = client
    c.post(
        "/discover/pick",
        data={"url": "https://www.youtube.com/shorts/pop", "platform": "youtube",
              "video_id": "pop"},
        follow_redirects=False,
    )
    fake_ytdlp(monkeypatch, entries_varied())
    body = c_get(client, "/discover?platform=youtube&run=1&hide_seen=1").text
    assert card_order(body) == ["slow", "tiny"]
    assert "ẩn 1 video đã xử lý" in body


def test_the_page_says_how_it_read_the_query(client, monkeypatch):
    """Gõ chữ trần ra tìm kiếm, gõ #tag ra hashtag — người dùng phải thấy được."""
    fake_ytdlp(monkeypatch, entries_varied())
    assert "nguồn <b>tìm kiếm</b>" in c_get(
        client, "/discover?platform=youtube&run=1&q=m%C3%A8o"
    ).text
    assert "nguồn <b>hashtag</b>" in c_get(
        client, "/discover?platform=youtube&run=1&q=%23shorts"
    ).text
    assert "nguồn <b>kênh</b>" in c_get(
        client, "/discover?platform=youtube&run=1&q=%40ai"
    ).text


def test_picking_a_video_also_starts_the_job(client):
    """"Chọn video này" nghĩa là "làm video này" — bắt nhảy ra terminal gõ
    `reup run` là cắt đôi một thao tác duy nhất."""
    c, _, db = client
    r = c.post(
        "/discover/pick",
        data={"url": "https://www.youtube.com/shorts/abc", "platform": "youtube",
              "video_id": "abc"},
        follow_redirects=False,
    )
    job_id = r.headers["location"].removeprefix("/jobs/")
    assert c.app.state.runner.started == [job_id]
