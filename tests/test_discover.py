import json
import subprocess
from pathlib import Path

import pytest

from reup.adapters.youtube import (
    MAX_DURATION_S,
    DiscoverError,
    YouTubeSource,
    feed_url,
    parse_entry,
)
from reup.cli import main
from reup.core.store import Store


def entry(vid: str, duration: float = 30, title: str = "Clip") -> str:
    return json.dumps(
        {"id": vid, "duration": duration, "title": title, "view_count": 1234,
         "upload_date": "20260901"}
    )


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


# --- phân tích kết quả ------------------------------------------------------

def test_entry_becomes_a_shorts_url():
    c = parse_entry(json.loads(entry("abc123")))
    assert c.url == "https://www.youtube.com/shorts/abc123"
    assert c.platform == "youtube"
    assert c.duration_ms == 30_000


def test_entry_without_id_is_dropped():
    assert parse_entry({"title": "không có id"}) is None


def test_video_longer_than_three_minutes_is_dropped():
    """Spec §3: đích là video dưới ~3 phút. Video dài cần hệ cắt clip riêng."""
    assert parse_entry(json.loads(entry("x", duration=MAX_DURATION_S + 1))) is None


def test_very_short_clip_is_dropped():
    assert parse_entry(json.loads(entry("x", duration=2))) is None


def test_entry_without_duration_is_kept():
    """Trang hashtag thỉnh thoảng không kèm độ dài; lọc lại được sau khi tải."""
    assert parse_entry({"id": "abc", "title": "x"}) is not None


def test_feed_url_uses_the_hashtag_page():
    """/feed/trending đã chết — yt-dlp bị YouTube đẩy về trang chủ."""
    assert feed_url("VN") == "https://www.youtube.com/hashtag/shorts"
    assert feed_url("VN", "cooking").endswith("/hashtag/cooking")


# --- quét -------------------------------------------------------------------

def test_lists_candidates(monkeypatch):
    fake_ytdlp(monkeypatch, "\n".join([entry("a"), entry("b")]))
    out = YouTubeSource().list_trending("VN", 5)
    assert [c.video_id for c in out] == ["a", "b"]


def test_stops_at_the_limit(monkeypatch):
    fake_ytdlp(monkeypatch, "\n".join(entry(f"v{i}") for i in range(20)))
    assert len(YouTubeSource().list_trending("VN", 3)) == 3


def test_long_videos_do_not_count_against_the_limit(monkeypatch):
    """Lọc sau khi lấy, nên phải xin dư từ yt-dlp."""
    lines = [entry("long1", 600), entry("ok1"), entry("long2", 900), entry("ok2")]
    fake_ytdlp(monkeypatch, "\n".join(lines))
    assert [c.video_id for c in YouTubeSource().list_trending("VN", 2)] == ["ok1", "ok2"]


def test_asks_for_more_than_the_limit(monkeypatch):
    seen = fake_ytdlp(monkeypatch, entry("a"))
    YouTubeSource().list_trending("VN", 5)
    assert seen["cmd"][seen["cmd"].index("--playlist-end") + 1] == "10"


def test_non_json_noise_is_skipped(monkeypatch):
    fake_ytdlp(monkeypatch, "WARNING: linh tinh\n" + entry("a") + "\nnothing")
    assert [c.video_id for c in YouTubeSource().list_trending("VN", 5)] == ["a"]


def test_failure_without_output_raises_with_advice(monkeypatch):
    fake_ytdlp(monkeypatch, "", code=1, stderr="does not exist")
    with pytest.raises(DiscoverError, match="reup add"):
        YouTubeSource().list_trending("VN", 5)


def test_partial_failure_with_output_is_kept(monkeypatch):
    """yt-dlp hay thoát khác 0 mà vẫn trả được vài dòng — đừng vứt chúng đi."""
    fake_ytdlp(monkeypatch, entry("a"), code=1, stderr="một lỗi lẻ")
    assert len(YouTubeSource().list_trending("VN", 5)) == 1


def test_zero_limit_is_rejected():
    with pytest.raises(ValueError, match="limit"):
        YouTubeSource().list_trending("VN", 0)


# --- lệnh CLI ---------------------------------------------------------------

def _args(tmp_path: Path, config_file: Path) -> list[str]:
    return [
        "--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
        "--db", str(tmp_path / "reup.db"),
    ]


def test_discover_lists_without_creating_jobs(
    tmp_path: Path, capsys, config_file: Path, monkeypatch
):
    fake_ytdlp(monkeypatch, entry("abc", title="Thịt kho tàu"))
    assert main([*_args(tmp_path, config_file), "discover"]) == 0
    out = capsys.readouterr().out
    assert "Thịt kho tàu" in out
    assert "--add" in out
    assert not (tmp_path / "jobs").exists() or not any((tmp_path / "jobs").iterdir())


def test_discover_add_creates_jobs(
    tmp_path: Path, capsys, config_file: Path, monkeypatch
):
    fake_ytdlp(monkeypatch, entry("abc"))
    assert main([*_args(tmp_path, config_file), "discover", "--add"]) == 0

    s = Store(tmp_path / "reup.db")
    assert len(s.list_jobs()) == 1
    assert s.is_seen("youtube", "abc")
    s.close()


def test_already_seen_videos_are_skipped(
    tmp_path: Path, capsys, config_file: Path, monkeypatch
):
    """Xử lý lại cùng một video là tốn tiền API và ra hai bản trùng nhau."""
    db = tmp_path / "reup.db"
    s = Store(db)
    s.init_schema()
    s.mark_seen("youtube", "abc")
    s.close()

    fake_ytdlp(monkeypatch, entry("abc"))
    main([*_args(tmp_path, config_file), "discover", "--add"])

    assert "đều đã xử lý rồi" in capsys.readouterr().out
    s = Store(db)
    assert s.list_jobs() == []
    s.close()


def test_discover_failure_returns_error_code(
    tmp_path: Path, capsys, config_file: Path, monkeypatch
):
    fake_ytdlp(monkeypatch, "", code=1, stderr="gãy")
    assert main([*_args(tmp_path, config_file), "discover"]) == 1
    assert "reup add" in capsys.readouterr().err
