# tests/test_stage_asr.py
from pathlib import Path
import pytest
from reup.models import Segment, Transcript
from reup.core.job import create_job
from reup.stages import asr as asr_stage


class _FakeASR:
    """Đứng chỗ của adapter thật. Stage chỉ biết interface, không biết engine."""

    name = "fake"

    def __init__(self, calls: dict, segments: list[Segment]) -> None:
        self.calls = calls
        self.segments = segments

    def transcribe(self, audio: Path, lang: str | None):
        self.calls["audio"] = audio
        self.calls["language"] = lang
        return "zh", self.segments


@pytest.fixture
def fake_transcribe(monkeypatch):
    calls = {}
    adapter = _FakeASR(
        calls,
        [
            Segment(id=1, start_ms=0, end_ms=3200, text="今天教大家做红烧肉"),
            Segment(id=2, start_ms=3400, end_ms=6000, text="先把肉切块"),
        ],
    )

    def _make_asr(cfg):
        calls["cfg"] = cfg
        return adapter

    monkeypatch.setattr(asr_stage, "make_asr", _make_asr)
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


def test_asr_does_not_own_transcript_json(tmp_path: Path, cfg_fixture, fake_transcribe):
    """transcript.json thuộc về reconcile — asr ghi vào đó là ghi đè bản đã hợp nhất."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.write_bytes(b"")

    asr_stage.run(job, cfg_fixture)

    assert not job.transcript_json.exists()


def test_asr_picks_engine_from_config(tmp_path: Path, cfg_fixture, fake_transcribe):
    """Engine do `asr.engine` quyết định, nên stage phải đi qua registry."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.write_bytes(b"")

    asr_stage.run(job, cfg_fixture)

    assert fake_transcribe["cfg"] is cfg_fixture
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
    monkeypatch.setattr(
        asr_stage, "make_asr", lambda cfg: _FakeASR({}, [])
    )

    with pytest.raises(ValueError, match="không nhận được câu nào"):
        asr_stage.run(job, cfg_fixture)


def test_spec_declares_its_artifacts():
    assert asr_stage.SPEC.name == "asr"
    assert set(asr_stage.SPEC.produces) == {"asr.json"}
