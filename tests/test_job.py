# tests/test_job.py
from datetime import datetime, timezone
from pathlib import Path
import pytest
from reup.core.job import Job, create_job, load_job, new_job_id


def test_job_id_is_sortable_and_stable():
    now = datetime(2026, 9, 11, 14, 30, 5, tzinfo=timezone.utc)
    a = new_job_id("https://douyin.com/video/123", now)
    b = new_job_id("https://douyin.com/video/123", now)
    assert a == b
    assert a.startswith("20260911-143005-")


def test_job_id_differs_by_url():
    now = datetime(2026, 9, 11, 14, 30, 5, tzinfo=timezone.utc)
    assert new_job_id("https://a/1", now) != new_job_id("https://a/2", now)


def test_create_job_makes_directories(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh")
    assert job.root.is_dir()
    assert job.audio_dir.is_dir()
    assert job.tts_dir.is_dir()
    assert job.render_dir.is_dir()


def test_artifact_paths_match_spec(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    assert job.source_video == tmp_path / "j1" / "source.mp4"
    assert job.full_16k == tmp_path / "j1" / "audio" / "full_16k.wav"
    assert job.full_48k == tmp_path / "j1" / "audio" / "full_48k.wav"
    assert job.vocals == tmp_path / "j1" / "audio" / "vocals.wav"
    assert job.bgm == tmp_path / "j1" / "audio" / "bgm.wav"
    assert job.asr_json == tmp_path / "j1" / "asr.json"
    assert job.final_mp4 == tmp_path / "j1" / "render" / "final.mp4"


def test_create_then_load_roundtrip(tmp_path: Path):
    created = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    loaded = load_job(tmp_path, "j1")
    assert loaded == created


def test_load_missing_job_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="khong-co"):
        load_job(tmp_path, "khong-co")


def test_tts_segment_path_is_zero_padded(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    assert job.tts_segment(7) == tmp_path / "j1" / "tts" / "seg_0007.wav"
    assert job.tts_segment(1234) == tmp_path / "j1" / "tts" / "seg_1234.wav"
