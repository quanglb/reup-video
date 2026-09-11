from pathlib import Path

import pytest

from reup.core.job import create_job
from reup.models import Segment, Transcript
from reup.stages import asr as asr_stage


@pytest.fixture
def fake_transcribe(monkeypatch):
    calls = {}

    def _fake(audio: Path, model: str, language: str | None):
        calls["audio"] = audio
        calls["model"] = model
        calls["language"] = language
        return "zh", [
            Segment(id=1, start_ms=0, end_ms=3200, text="今天教大家做红烧肉"),
            Segment(id=2, start_ms=3400, end_ms=6000, text="先把肉切块"),
        ]

    monkeypatch.setattr(asr_stage, "transcribe", _fake)
    return calls


def test_asr_writes_asr_json(tmp_path: Path, cfg_fixture, fake_transcribe):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.write_bytes(b"")

    asr_stage.run(job, cfg_fixture)

    t = Transcript.load(job.asr_json)
    assert t.source_lang == "zh"
    assert len(t.segments) == 2
    assert t.segments[0].text == "今天教大家做红烧肉"
    assert t.segments[0].text_source == "asr"


def test_asr_also_seeds_transcript_json(tmp_path: Path, cfg_fixture, fake_transcribe):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.write_bytes(b"")

    asr_stage.run(job, cfg_fixture)

    assert Transcript.load(job.transcript_json) == Transcript.load(job.asr_json)


def test_asr_passes_model_from_profile(tmp_path: Path, cfg_fixture, fake_transcribe):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.write_bytes(b"")

    asr_stage.run(job, cfg_fixture)

    assert fake_transcribe["model"] == "tiny"
    assert fake_transcribe["audio"] == job.full_16k
    assert fake_transcribe["language"] == "zh"


def test_asr_passes_none_language_when_auto(tmp_path: Path, cfg_fixture, fake_transcribe):
    job = create_job(tmp_path / "jobs", "https://a/1", "auto", job_id="j1")
    job.full_16k.write_bytes(b"")

    asr_stage.run(job, cfg_fixture)

    assert fake_transcribe["language"] is None


def test_asr_rejects_empty_transcript(tmp_path: Path, cfg_fixture, monkeypatch):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.write_bytes(b"")
    monkeypatch.setattr(asr_stage, "transcribe", lambda a, m, l: ("zh", []))

    with pytest.raises(ValueError, match="không nhận được câu nào"):
        asr_stage.run(job, cfg_fixture)


def test_spec_declares_its_artifacts():
    assert asr_stage.SPEC.name == "asr"
    assert set(asr_stage.SPEC.produces) == {"asr.json", "transcript.json"}
