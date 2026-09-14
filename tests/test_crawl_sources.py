"""Crawler TikTok và Douyin: dựng URL, đọc entry, và lời báo lỗi khi thiếu cookie."""
import json
import subprocess

import pytest

from reup.adapters import douyin, tiktok
from reup.adapters.crawl import DiscoverError, cookie_args, dump_flat
from reup.adapters.registry import make_source
from reup.config import DiscoverConfig, PlatformDiscoverConfig


def fake_ytdlp(monkeypatch, stdout: str, code: int = 0, stderr: str = ""):
    seen = {}
    real = subprocess.run

    def run(cmd, **kwargs):
        if cmd[0] != "yt-dlp":
            return real(cmd, **kwargs)
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", run)
    return seen


# --- dựng URL nguồn ---------------------------------------------------------

def test_tiktok_hashtag_becomes_a_tag_page():
    assert tiktok.feed_url("xuhuong") == "https://www.tiktok.com/tag/xuhuong"
    assert tiktok.feed_url("#xuhuong") == "https://www.tiktok.com/tag/xuhuong"


def test_tiktok_user_and_full_url_pass_through():
    assert tiktok.feed_url("@ai") == "https://www.tiktok.com/@ai"
    assert tiktok.feed_url("https://www.tiktok.com/tag/x") == "https://www.tiktok.com/tag/x"


def test_douyin_without_a_query_says_what_is_missing():
    """Douyin không có trang trending mở, nên query rỗng là lỗi cấu hình."""
    with pytest.raises(ValueError, match="id người dùng hoặc URL"):
        douyin.feed_url("")


# --- đọc entry --------------------------------------------------------------

def test_tiktok_entry_carries_an_embed_url():
    c = tiktok.parse_entry({"id": "7311", "duration": 20, "title": "Clip", "uploader": "@ai"})
    assert c.embed_url == "https://www.tiktok.com/embed/v2/7311"
    assert c.url == "https://www.tiktok.com/@ai/video/7311"
    assert c.uploader == "ai"


def test_video_longer_than_three_minutes_is_dropped_on_every_platform():
    long_one = {"id": "1", "duration": 400}
    assert tiktok.parse_entry(long_one) is None
    assert douyin.parse_entry(long_one) is None


def test_entry_without_duration_is_kept():
    """Trang liệt kê của TikTok hay bỏ trống độ dài; lọc sau khi tải còn hơn bỏ sót."""
    assert tiktok.parse_entry({"id": "9", "title": "x"}) is not None


def test_douyin_entry_uses_the_open_player():
    c = douyin.parse_entry({"id": "abc", "duration": 30, "title": "标题"})
    assert c.embed_url == "https://open.douyin.com/player/video?vid=abc&autoplay=0"
    assert c.url == "https://www.douyin.com/video/abc"


# --- cookie -----------------------------------------------------------------

def test_cookie_file_wins_over_browser():
    assert cookie_args("chrome", "/tmp/c.txt") == ["--cookies", "/tmp/c.txt"]
    assert cookie_args("chrome", None) == ["--cookies-from-browser", "chrome"]
    assert cookie_args(None, None) == []


def test_tiktok_passes_cookies_to_ytdlp(monkeypatch):
    seen = fake_ytdlp(monkeypatch, json.dumps({"id": "1", "duration": 20}))
    tiktok.TikTokSource("x", cookies_from_browser="chrome").list_trending("VN", 1)
    assert "--cookies-from-browser" in seen["cmd"]


def test_tiktok_without_cookies_blames_the_login_wall(monkeypatch):
    """Lỗi trơn "quét hỏng" không sửa được gì; phải nói ra chỗ đặt cookie."""
    fake_ytdlp(monkeypatch, "", code=1, stderr="Unable to extract")
    with pytest.raises(DiscoverError, match="cookies_from_browser"):
        tiktok.TikTokSource("x").list_trending("VN", 5)


def test_douyin_error_mentions_the_china_ip_requirement(monkeypatch):
    fake_ytdlp(monkeypatch, "", code=1, stderr="403")
    with pytest.raises(DiscoverError, match="IP ra được Trung Quốc"):
        douyin.DouyinSource("MS4wLjABAAAAuser123").list_trending("VN", 5)


def test_douyin_keyword_points_to_the_export_flow():
    """Gõ "抖音" vào ô quét từng ra "Unsupported URL" của yt-dlp — không chỉ được
    người dùng sang đường đúng là nạp file xuất."""
    with pytest.raises(ValueError, match="không tìm theo từ khoá"):
        douyin.feed_url("抖音")


def test_missing_ytdlp_is_reported_plainly(monkeypatch):
    def boom(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(DiscoverError, match="không tìm thấy yt-dlp"):
        dump_flat("https://x", 3)


# --- registry ---------------------------------------------------------------

def test_registry_builds_each_platform(cfg_fixture):
    cfg = cfg_fixture
    object.__setattr__(
        cfg,
        "discover",
        DiscoverConfig(
            youtube=PlatformDiscoverConfig(query="anime"),
            tiktok=PlatformDiscoverConfig(query="xuhuong", cookies_from_browser="chrome"),
            douyin=PlatformDiscoverConfig(query="u1"),
        ),
    )
    assert make_source(cfg, "youtube").query == "anime"
    assert make_source(cfg, "tiktok").cookies_from_browser == "chrome"
    assert make_source(cfg, "douyin", "u2").query == "u2"


def test_registry_rejects_an_unknown_platform(cfg_fixture):
    with pytest.raises(ValueError, match="không có nền tảng"):
        make_source(cfg_fixture, "instagram")
