"""Tải video Douyin khi link trong file xuất đã hết hạn."""
import json
import os
from pathlib import Path

import pytest

from reup.adapters import douyin_export
from reup.adapters.manual import ManualSource
from reup.config import load_config
from reup.core.job import create_job
from reup.stages import fetch as fetch_stage

from tests.test_douyin_export import export, video


def setup(tmp_path: Path, old_link="https://v5.zjcdn.com/cu.mp4"):
    jobs = tmp_path / "jobs"
    job = create_job(jobs, "https://www.douyin.com/video/abc", "zh", job_id="j1")
    douyin_export.save_direct(job.root, old_link, video_id="abc", url=job.source_url, title="菜")
    return job


def write_export(folder: Path, name: str, link: str, age_s: int = 0):
    folder.mkdir(parents=True, exist_ok=True)
    raw = export(video("abc", 1, 5, play_url=link), video("khac", 2, 1))
    p = folder / name
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    t = p.stat().st_mtime - age_s
    os.utime(p, (t, t))
    return p


def test_fresher_link_comes_from_the_newest_export(tmp_path):
    job = setup(tmp_path)
    folder = fetch_stage.exports_dir(job)
    write_export(folder, "old.json", "https://v5.zjcdn.com/cu.mp4", age_s=3600)
    write_export(folder, "new.json", "https://v5.zjcdn.com/moi.mp4")

    assert fetch_stage.fresher_link(job) == "https://v5.zjcdn.com/moi.mp4"
    saved = json.loads((job.root / douyin_export.DIRECT_FILE).read_text(encoding="utf-8"))
    assert saved["media_url"] == "https://v5.zjcdn.com/moi.mp4"


def test_no_fresher_link_when_exports_only_have_the_same_one(tmp_path):
    job = setup(tmp_path)
    write_export(fetch_stage.exports_dir(job), "same.json", "https://v5.zjcdn.com/cu.mp4")
    assert fetch_stage.fresher_link(job) is None


def test_expired_link_is_retried_with_the_fresher_one(tmp_path, config_file, monkeypatch):
    job = setup(tmp_path)
    write_export(fetch_stage.exports_dir(job), "new.json", "https://v5.zjcdn.com/moi.mp4")
    tried = []

    def fake_download(url, dest, timeout=120.0):
        tried.append(url)
        if url.endswith("cu.mp4"):
            raise OSError("HTTP Error 403: Forbidden")
        Path(dest).write_bytes(b"video")

    monkeypatch.setattr(douyin_export, "download", fake_download)
    monkeypatch.setattr(ManualSource, "fetch", lambda *a, **k: pytest.fail("không được tới yt-dlp"))
    fetch_stage._download(job, load_config(config_file))
    assert tried == ["https://v5.zjcdn.com/cu.mp4", "https://v5.zjcdn.com/moi.mp4"]
    assert job.source_video.read_bytes() == b"video"


def test_all_failing_gives_clear_fix_instructions(tmp_path, config_file, monkeypatch):
    job = setup(tmp_path)

    def expired(url, dest, timeout=120.0):
        raise OSError("HTTP Error 403: Forbidden")

    def ytdlp_fails(self, url, dest):
        raise RuntimeError("Fresh cookies (not necessarily logged in) are needed")

    monkeypatch.setattr(douyin_export, "download", expired)
    monkeypatch.setattr(ManualSource, "fetch", ytdlp_fails)
    with pytest.raises(RuntimeError) as err:
        fetch_stage._download(job, load_config(config_file))
    text = str(err.value)
    assert "hết hạn" in text and "CÁCH SỬA" in text and "cookies_from_browser" in text


def test_ytdlp_gets_douyin_cookies_from_config(tmp_path, config_file, monkeypatch):
    job = setup(tmp_path)
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(
        text.replace('[discover.douyin]\nquery = ""\nlimit = 12\ncookies_from_browser = ""',
                     '[discover.douyin]\nquery = ""\nlimit = 12\ncookies_from_browser = "chrome"'),
        encoding="utf-8",
    )
    monkeypatch.setattr(douyin_export, "download",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("403")))
    seen = {}

    def capture(self, url, dest):
        seen["cookies"] = self.cookies_from_browser
        Path(dest).write_bytes(b"v")

    monkeypatch.setattr(ManualSource, "fetch", capture)
    fetch_stage._download(job, load_config(config_file))
    assert seen["cookies"] == "chrome"


def test_manual_source_passes_cookie_args_to_ytdlp(monkeypatch, tmp_path):
    import subprocess

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        (tmp_path / "out.info.json").write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ManualSource(cookies_from_browser="chrome").fetch("https://www.douyin.com/video/abc", tmp_path / "out.mp4")
    cmd = captured["cmd"]
    assert cmd[cmd.index("--cookies-from-browser") + 1] == "chrome"
