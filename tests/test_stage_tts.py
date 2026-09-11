import json
from pathlib import Path

import pytest

from reup.core.job import create_job
from reup.models import Segment, Transcript
from reup.stages import tts as tts_stage


def _seed(job, texts: list[str], lang: str = "zh") -> None:
    Transcript(
        source_lang=lang,
        segments=[
            Segment(id=i, start_ms=(i - 1) * 3000, end_ms=i * 3000, text=t)
            for i, t in enumerate(texts, start=1)
        ],
    ).save(job.transcript_json)


def test_writes_one_wav_per_segment(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["今天教大家", "先把肉切块"])

    tts_stage.run(job, cfg_fixture)

    assert job.tts_segment(1).exists()
    assert job.tts_segment(2).exists()


def test_manifest_records_actual_durations(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["今天教大家"])

    tts_stage.run(job, cfg_fixture)

    manifest = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["voice"] == "stub-zh-1"
    assert manifest["lang"] == "zh"
    assert len(manifest["segments"]) == 1
    entry = manifest["segments"][0]
    assert entry["id"] == 1
    assert entry["actual_ms"] > 0
    assert entry["path"] == "seg_0001.wav"


def test_rerun_regenerates_all_segments(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["今天教大家"])
    tts_stage.run(job, cfg_fixture)
    first = job.tts_segment(1).stat().st_size

    _seed(job, ["今天教大家做红烧肉先把肉切块"])
    tts_stage.run(job, cfg_fixture)

    assert job.tts_segment(1).stat().st_size > first


def test_fails_on_empty_transcript(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    Transcript(source_lang="zh", segments=[]).save(job.transcript_json)

    with pytest.raises(ValueError, match="không có câu nào"):
        tts_stage.run(job, cfg_fixture)


def test_spec_declares_its_artifacts():
    assert tts_stage.SPEC.name == "tts"
    assert set(tts_stage.SPEC.produces) == {"tts/manifest.json"}
