"""File JSON xuất từ console Douyin: đọc, sắp theo like, cắt theo vị trí, tải thẳng."""
import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reup.adapters import douyin_export
from reup.adapters.crawl import order_and_slice
from reup.adapters.douyin import DouyinSource
from reup.adapters.manual import ManualSource
from reup.adapters.source import FetchResult
from reup.cli import main
from reup.core.job import create_job
from reup.core.store import Store
from reup.stages import fetch as fetch_stage
from reup.web.app import create_app

from tests.test_web_app import FakeRunner


def video(aid: str, position: int, likes: int, **extra) -> dict:
    return {
        "position": position,
        "aweme_id": aid,
        "desc": f"视频 {aid}",
        "digg_count": likes,
        "share_count": likes // 10,
        "play_count": 0,
        "duration_ms": 30_000,
        "create_time": 1_757_000_000,
        "author": "厨师",
        "cover": "http://p3.douyinpic.com/c.jpg",
        "play_url": f"http://v26.douyinvod.com/{aid}.mp4",
        **extra,
    }


def export(*videos: dict) -> dict:
    return {"format": douyin_export.FORMAT, "sec_user_id": "MS4wLjABAAAA_x", "videos": list(videos)}


def three() -> dict:
    return export(video("a", 1, 50), video("b", 2, 9_000), video("c", 3, 700))


@pytest.fixture
def export_file(tmp_path: Path) -> Path:
    p = tmp_path / "douyin_x.json"
    p.write_text(json.dumps(three(), ensure_ascii=False), encoding="utf-8")
    return p


# --- đọc file ---------------------------------------------------------------

def test_export_keeps_channel_order_and_the_numbers():
    found = douyin_export.parse_export(three())
    assert [c.video_id for c in found] == ["a", "b", "c"]
    b = found[1]
    assert (b.like_count, b.share_count, b.position) == (9_000, 900, 2)
    assert b.url == "https://www.douyin.com/video/b"
    assert b.published_at == "20250904"


def test_http_links_are_upgraded_to_https():
    """Script gốc cũng đổi http->https: CDN trả http nhưng trang https chặn mixed content."""
    c = douyin_export.parse_export(three())[0]
    assert c.media_url == "https://v26.douyinvod.com/a.mp4"
    assert c.thumbnail.startswith("https://")


def test_posts_without_video_and_overlong_videos_are_dropped():
    found = douyin_export.parse_export(
        export(video("ok", 1, 1), video("img", 2, 1, play_url=""), video("long", 3, 1, duration_ms=400_000))
    )
    assert [c.video_id for c in found] == ["ok"]


def test_a_foreign_json_file_is_rejected_with_a_way_out():
    with pytest.raises(ValueError, match="Chạy lại script ở tab Douyin"):
        douyin_export.parse_export({"videos": []})


def test_douyin_source_reads_an_export_without_calling_ytdlp(export_file, monkeypatch):
    def boom(cmd, **kwargs):
        raise AssertionError("file xuất không được đi qua yt-dlp")

    monkeypatch.setattr(subprocess, "run", boom)
    source = DouyinSource(str(export_file))
    assert source.lists_all
    assert source.describe() == (str(export_file), "file xuất")
    assert len(source.list_trending("VN", 1)) == 3  # cả kênh, người gọi tự cắt


# --- sắp và cắt --------------------------------------------------------------

def test_sorting_by_likes_ranks_the_whole_channel_before_slicing():
    found = douyin_export.parse_export(three())
    assert [c.video_id for c in order_and_slice(found, "likes", 1, 2)] == ["b", "c"]
    assert [c.video_id for c in order_and_slice(found, "likes", 2, 5)] == ["c", "a"]


def test_start_without_sort_is_the_channel_position():
    found = douyin_export.parse_export(three())
    assert [c.video_id for c in order_and_slice(found, "", 2, 1)] == ["b"]


def test_start_below_one_is_an_error():
    with pytest.raises(ValueError, match="vị trí bắt đầu"):
        order_and_slice([], "", 0, 5)


# --- tải ---------------------------------------------------------------------

def test_direct_link_must_be_https(tmp_path):
    with pytest.raises(ValueError, match="https"):
        douyin_export.save_direct(tmp_path, "file:///etc/passwd")


def test_fetch_stage_downloads_straight_from_the_cdn(tmp_path, monkeypatch, sample_video, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://www.douyin.com/video/b", "zh", job_id="j1")
    douyin_export.save_direct(job.root, "https://v26.douyinvod.com/b.mp4", video_id="b", title="美食 做法")
    seen = {}

    def fake_download(url, dest, timeout=120.0):
        seen["url"] = url
        Path(dest).write_bytes(sample_video.read_bytes())

    def no_ytdlp(self, url, dest):
        raise AssertionError("có link tải thẳng thì không cần yt-dlp")

    monkeypatch.setattr(douyin_export, "download", fake_download)
    monkeypatch.setattr(ManualSource, "fetch", no_ytdlp)
    fetch_stage.run(job, cfg_fixture)

    assert seen["url"] == "https://v26.douyinvod.com/b.mp4"
    info = json.loads(job.source_info.read_text(encoding="utf-8"))
    assert info["title"] == "美食 做法"  # translate đoán thể loại từ đây


def test_expired_link_falls_back_to_ytdlp(tmp_path, monkeypatch, sample_video, cfg_fixture):
    """Link CDN hết hạn sau vài giờ; job chọn từ hôm qua vẫn phải có đường tải."""
    job = create_job(tmp_path / "jobs", "https://www.douyin.com/video/b", "zh", job_id="j1")
    douyin_export.save_direct(job.root, "https://v26.douyinvod.com/b.mp4")

    def expired(url, dest, timeout=120.0):
        raise OSError("HTTP Error 403: Forbidden")

    def fake_fetch(self, url, dest):
        dest.write_bytes(sample_video.read_bytes())
        info = dest.parent / "source.info.json"
        info.write_text("{}", encoding="utf-8")
        return FetchResult(dest, info, 6000, 540, 960)

    monkeypatch.setattr(douyin_export, "download", expired)
    monkeypatch.setattr(ManualSource, "fetch", fake_fetch)
    fetch_stage.run(job, cfg_fixture)
    assert job.source_video.exists()


def test_when_both_paths_fail_both_reasons_are_reported(tmp_path, monkeypatch, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://www.douyin.com/video/b", "zh", job_id="j1")
    douyin_export.save_direct(job.root, "https://v26.douyinvod.com/b.mp4")

    def expired(url, dest, timeout=120.0):
        raise OSError("HTTP Error 403")

    def ytdlp_fails(self, url, dest):
        raise RuntimeError("yt-dlp thoát với mã 1")

    monkeypatch.setattr(douyin_export, "download", expired)
    monkeypatch.setattr(ManualSource, "fetch", ytdlp_fails)
    with pytest.raises(RuntimeError) as err:
        fetch_stage.run(job, cfg_fixture)
    assert "403" in str(err.value) and "yt-dlp thoát" in str(err.value)


# --- Web UI ------------------------------------------------------------------

@pytest.fixture
def client(tmp_path: Path, config_file: Path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    return TestClient(app), jobs, db


def test_douyin_tab_offers_the_console_script(client):
    c, _, _ = client
    body = c.get("/discover?platform=douyin").text
    assert "Chép script" in body
    assert "aweme/v1/web/aweme/post" in body


def test_uploading_an_export_lists_it_sorted_by_likes(client):
    c, _, _ = client
    r = c.post(
        "/discover/douyin/import",
        files={"file": ("douyin_x.json", json.dumps(three()), "application/json")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    body = c.get(r.headers["location"] + "&sort=likes&start=1&limit=2").text
    assert re.findall(r'name="video_id" value="([^"]+)"', body) == ["b", "c"]
    assert "9K ♥" in body
    assert "(từ vị trí 1 / 3)" in body


def test_imported_files_are_offered_again_as_suggestions(client):
    """Nạp một lần, quét lại nhiều lần với cách sắp khác — không bắt nạp lại."""
    c, _, _ = client
    data = {**three(), "author": "厨师小王"}
    c.post(
        "/discover/douyin/import",
        files={"file": ("douyin_x.json", json.dumps(data), "application/json")},
        follow_redirects=False,
    )
    body = c.get("/discover?platform=douyin").text
    assert "File đã nạp" in body
    assert "厨师小王" in body and "3 video" in body


def test_douyin_topics_open_the_channel_search(client):
    c, _, _ = client
    body = c.get("/discover?platform=douyin").text
    assert "https://www.douyin.com/search/%E7%BE%8E%E9%A3%9F%20%E5%88%B6%E4%BD%9C?type=user" in body
    assert "File đã nạp" not in body  # chưa nạp gì thì không hiện khung trống


def test_broken_files_are_not_suggested(tmp_path):
    (tmp_path / "hong.json").write_text("{", encoding="utf-8")
    (tmp_path / "la.json").write_text("{}", encoding="utf-8")
    assert douyin_export.recent_exports(tmp_path) == []


def test_uploading_a_wrong_file_says_why(client):
    c, _, _ = client
    r = c.post("/discover/douyin/import", files={"file": ("x.json", "{}", "application/json")})
    assert r.status_code == 400
    assert "reup-douyin/1" in r.text


def test_picking_an_exported_video_keeps_its_direct_link(client):
    c, jobs, _ = client
    r = c.post(
        "/discover/pick",
        data={
            "url": "https://www.douyin.com/video/b",
            "platform": "douyin",
            "video_id": "b",
            "media_url": "https://v26.douyinvod.com/b.mp4",
            "title": "视频 b",
        },
        follow_redirects=False,
    )
    job_id = r.headers["location"].removeprefix("/jobs/")
    meta = json.loads((jobs / job_id / douyin_export.DIRECT_FILE).read_text(encoding="utf-8"))
    assert meta["media_url"] == "https://v26.douyinvod.com/b.mp4"


# --- CLI ---------------------------------------------------------------------

def test_cli_adds_top_liked_videos_from_a_position(tmp_path, config_file, export_file, capsys):
    jobs, db = tmp_path / "jobs", tmp_path / "reup.db"
    args = ["--config", str(config_file), "--jobs-dir", str(jobs), "--db", str(db),
            "--env", str(tmp_path / ".env")]
    code = main([*args, "discover", "--platform", "douyin", "--query", str(export_file),
                 "--sort", "likes", "--start", "2", "--limit", "1", "--add"])
    assert code == 0
    s = Store(db)
    rows = s.list_jobs()
    s.close()
    assert [r["url"] for r in rows] == ["https://www.douyin.com/video/c"]
    assert (jobs / rows[0]["id"] / douyin_export.DIRECT_FILE).exists()
