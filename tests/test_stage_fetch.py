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
