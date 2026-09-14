# tests/test_stage_fetch.py
from pathlib import Path
import pytest
from reup.adapters.manual import ManualSource
from reup.adapters.source import Candidate, FetchResult
from reup.core.job import create_job
from reup.stages import fetch as fetch_stage


def test_manual_source_has_name():
    assert ManualSource().name == "manual"


def test_manual_source_cannot_list_trending():
    with pytest.raises(NotImplementedError, match="manual"):
        ManualSource().list_trending("VN", 10)


def test_fetch_stage_writes_video_and_info(tmp_path: Path, monkeypatch, sample_video: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")

    def fake_fetch(self, url: str, dest: Path) -> FetchResult:
        dest.write_bytes(sample_video.read_bytes())
        info = dest.parent / "source.info.json"
        info.write_text('{"title": "thịt kho tàu"}', encoding="utf-8")
        return FetchResult(dest, info, 6000, 540, 960)

    monkeypatch.setattr(ManualSource, "fetch", fake_fetch)
    fetch_stage.run(job, cfg_fixture)

    assert job.source_video.exists()
    assert job.source_info.exists()
    assert "thịt kho tàu" in job.source_info.read_text(encoding="utf-8")


def test_fetch_stage_rejects_video_without_audio(tmp_path: Path, monkeypatch, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")

    def fake_fetch(self, url: str, dest: Path) -> FetchResult:
        import subprocess
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=540x960:rate=30:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(dest),
        ], check=True, capture_output=True)
        info = dest.parent / "source.info.json"
        info.write_text("{}", encoding="utf-8")
        return FetchResult(dest, info, 2000, 540, 960)

    monkeypatch.setattr(ManualSource, "fetch", fake_fetch)
    with pytest.raises(ValueError, match="không có audio"):
        fetch_stage.run(job, cfg_fixture)


def test_spec_declares_its_artifacts():
    assert fetch_stage.SPEC.name == "fetch"
    assert set(fetch_stage.SPEC.produces) == {"source.mp4", "source.info.json"}


def test_candidate_is_hashable():
    c = Candidate("douyin", "v1", "https://a/1", "tiêu đề", 30000, 5000, "2026-09-01")
    assert {c, c} == {c}


def test_default_selector_takes_the_smallest_mp4_including_av1():
    """Trên M4 hardware AV1 decode gần như miễn phí, mà bản AV1 nhỏ hơn một nửa."""
    from reup.adapters.manual import FORMAT_SELECTOR, format_selector

    assert format_selector(prefer_h264=False) == FORMAT_SELECTOR
    assert "avc1" not in FORMAT_SELECTOR


def test_h264_selector_puts_avc1_before_bare_mp4():
    """AV1 vẫn khớp `ext=mp4`, nên nhánh avc1 phải đứng trước mới có tác dụng."""
    from reup.adapters.manual import FORMAT_SELECTOR_H264

    branches = FORMAT_SELECTOR_H264.split("/")
    first_avc1 = next(i for i, b in enumerate(branches) if "avc1" in b)
    first_bare_mp4 = next(
        i for i, b in enumerate(branches) if "ext=mp4" in b and "avc1" not in b
    )
    assert first_avc1 < first_bare_mp4


@pytest.mark.parametrize("prefer", [False, True])
def test_every_selector_has_a_catch_all(prefer):
    """Nguồn nào không có nhánh ưu tiên (Douyin, TikTok) vẫn phải tải được."""
    from reup.adapters.manual import format_selector

    assert format_selector(prefer).split("/")[-1] == "b"


@pytest.mark.parametrize("prefer", [False, True])
def test_fetch_passes_the_right_selector_to_yt_dlp(tmp_path: Path, monkeypatch, prefer):
    """Selector phải thật sự đi vào dòng lệnh, không chỉ nằm trong hằng số."""
    import subprocess
    from reup.adapters.manual import format_selector

    seen = {}

    class FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        dest = Path(cmd[cmd.index("-o") + 1])
        dest.write_bytes(b"x")
        (dest.parent / "source.info.json").write_text(
            '{"duration": 18, "width": 1080, "height": 1920}', encoding="utf-8"
        )
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ManualSource(prefer_h264=prefer).fetch("https://a/1", tmp_path / "source.mp4")

    assert seen["cmd"][seen["cmd"].index("-f") + 1] == format_selector(prefer)
    assert result.width == 1080
    assert result.height == 1920
    assert result.duration_ms == 18_000


def test_fetch_stage_honours_prefer_h264_from_profile(tmp_path: Path, monkeypatch, cfg_fixture):
    """Knob trong profile phải đi tới adapter, không bị stage bỏ qua."""
    from dataclasses import replace
    from reup.adapters.manual import FORMAT_SELECTOR_H264
    import subprocess

    seen = {}

    class FakeProc:
        returncode = 0
        stderr = ""

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        # probe() cũng dùng subprocess.run — chỉ chặn yt-dlp, để ffprobe chạy thật
        if cmd[0] != "yt-dlp":
            return real_run(cmd, **kwargs)
        seen["cmd"] = cmd
        dest = Path(cmd[cmd.index("-o") + 1])
        dest.write_bytes(b"x")
        (dest.parent / "source.info.json").write_text("{}", encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    cfg = replace(cfg_fixture, profile=replace(cfg_fixture.profile, prefer_h264=True))

    # probe sẽ gãy vì file giả, nhưng lệnh yt-dlp đã dựng xong trước đó
    with pytest.raises(Exception):
        fetch_stage.run(job, cfg)

    assert seen["cmd"][seen["cmd"].index("-f") + 1] == FORMAT_SELECTOR_H264


# --- JS challenge của YouTube -----------------------------------------------

def test_js_runtime_is_auto_detected(monkeypatch):
    """deno là runtime duy nhất yt-dlp tự bật; node và bun phải khai bằng tên."""
    import shutil
    from reup.adapters.manual import js_runtime_args

    monkeypatch.setattr(shutil, "which", lambda n: "/bin/node" if n == "node" else None)
    assert js_runtime_args() == ["--js-runtimes", "node"]


def test_deno_wins_when_several_runtimes_exist(monkeypatch):
    import shutil
    from reup.adapters.manual import js_runtime_args

    monkeypatch.setattr(shutil, "which", lambda n: f"/bin/{n}")
    assert js_runtime_args() == ["--js-runtimes", "deno"]


def test_an_explicit_runtime_beats_detection(monkeypatch):
    import shutil
    from reup.adapters.manual import js_runtime_args

    monkeypatch.setattr(shutil, "which", lambda n: f"/bin/{n}")
    assert js_runtime_args("bun") == ["--js-runtimes", "bun"]


def test_no_runtime_means_no_flag(monkeypatch):
    """Không bịa cờ khi máy trống: để yt-dlp than bằng lời của nó."""
    import shutil
    from reup.adapters.manual import js_runtime_args

    monkeypatch.setattr(shutil, "which", lambda n: None)
    assert js_runtime_args() == []


def test_remote_components_can_be_turned_off():
    """Tắt đi là không có mã tải từ GitHub chạy trên máy."""
    from reup.adapters.manual import youtube_args

    assert "--remote-components" not in youtube_args("node", remote_components="")
    assert youtube_args("node")[-2:] == ["--remote-components", "ejs:github"]


def test_fetch_asks_yt_dlp_to_solve_the_js_challenge(tmp_path: Path, monkeypatch):
    """Thiếu hai cờ này thì MỌI video YouTube báo 'This video is not available'."""
    import subprocess

    seen = {}

    class FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        dest = Path(cmd[cmd.index("-o") + 1])
        dest.write_bytes(b"x")
        (dest.parent / "source.info.json").write_text("{}", encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ManualSource(js_runtime="node").fetch("https://a/1", tmp_path / "source.mp4")

    cmd = seen["cmd"]
    assert cmd[cmd.index("--js-runtimes") + 1] == "node"
    assert cmd[cmd.index("--remote-components") + 1] == "ejs:github"


def test_fetch_stage_honours_the_fetch_config(tmp_path: Path, monkeypatch, cfg_fixture):
    """Knob trong config phải đi tới adapter, không bị stage bỏ qua."""
    from dataclasses import replace
    import subprocess

    from reup.config import FetchConfig
    from reup.core.job import create_job
    from reup.stages.fetch import run as fetch_run

    seen = {}

    class FakeProc:
        returncode = 0
        stderr = ""

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] != "yt-dlp":
            return real_run(cmd, **kwargs)
        seen["cmd"] = cmd
        dest = Path(cmd[cmd.index("-o") + 1])
        dest.write_bytes(b"x")
        (dest.parent / "source.info.json").write_text("{}", encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    job = create_job(tmp_path / "jobs", "https://a/1", "auto")
    cfg = replace(cfg_fixture, fetch=FetchConfig(js_runtime="bun", remote_components=""))
    try:
        fetch_run(job, cfg)
    except Exception:
        pass  # probe() gãy vì file giả — chỉ quan tâm dòng lệnh yt-dlp

    assert seen["cmd"][seen["cmd"].index("--js-runtimes") + 1] == "bun"
    assert "--remote-components" not in seen["cmd"]
