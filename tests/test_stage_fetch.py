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
