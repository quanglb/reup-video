"""Vòng viết lại của stage fit — spec §7.7.

Đây là chỗ duy nhất trong pipeline có vòng lặp, nên cũng là chỗ duy nhất có thể
lặp vô tận. Mọi test dưới đây đều canh chừng điều đó.
"""
import json
from pathlib import Path

import pytest

from reup.adapters.stub_tts import StubTTS
from reup.adapters.tts import TTSResult
from reup.core.job import create_job
from reup.models import Segment, Transcript
from reup.stages import fit as fit_stage
from reup.stages import tts as tts_stage


class ShrinkingLLM:
    """Mỗi lần được gọi lại trả một câu ngắn hơn một âm tiết."""

    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.calls = 0

    def complete_json(self, prompt, schema):
        self.calls += 1
        return {"text": self.texts.pop(0)}


class StubbornLLM:
    """Không chịu rút ngắn — dùng để kiểm tra ngân sách viết lại là cứng."""

    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def complete_json(self, prompt, schema):
        self.calls += 1
        return {"text": self.text}


def _seed_and_synth(job, cfg, start, end, text):
    Transcript(
        source_lang="vi",
        segments=[Segment(id=1, start_ms=start, end_ms=end, text=text)],
    ).save(job.translation_json)
    tts_stage.run_with(job, cfg, StubTTS())


# StubTTS: 220ms mỗi âm tiết. Khe 1000ms -> vừa khít khoảng 4 âm tiết.
LONG = "một hai ba bốn năm sáu bảy tám chín mười"  # 10 âm tiết = 2200ms
MID = "một hai ba bốn năm"  # 5 âm tiết = 1100ms
SHORT = "một hai ba"  # 3 âm tiết = 660ms


def test_rewrite_is_attempted_when_segment_is_too_long(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed_and_synth(job, cfg_fixture, 0, 1000, LONG)  # ratio 2.2 -> rewrite
    llm = ShrinkingLLM([SHORT])

    fit_stage.run_with(job, cfg_fixture, StubTTS(), llm)

    assert llm.calls == 1
    assert Transcript.load(job.translation_json).segments[0].text == SHORT


def test_rewritten_text_is_resynthesized(tmp_path: Path, cfg_fixture):
    """Viết lại mà không đọc lại thì track lồng tiếng vẫn là câu cũ."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed_and_synth(job, cfg_fixture, 0, 1000, LONG)
    before = job.tts_segment(1).stat().st_size

    fit_stage.run_with(job, cfg_fixture, StubTTS(), ShrinkingLLM([SHORT]))

    assert job.tts_segment(1).stat().st_size < before


def test_rewrite_budget_is_hard_at_two(tmp_path: Path, cfg_fixture):
    """LLM cứng đầu không được kéo pipeline vào vòng lặp vô tận."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed_and_synth(job, cfg_fixture, 0, 1000, LONG)
    llm = StubbornLLM(LONG)

    fit_stage.run_with(job, cfg_fixture, StubTTS(), llm)

    assert llm.calls == 2  # MAX_REVISIONS, không hơn
    assert "overflow" in Transcript.load(job.translation_json).segments[0].flags


def test_revision_count_is_recorded(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed_and_synth(job, cfg_fixture, 0, 1000, LONG)

    fit_stage.run_with(job, cfg_fixture, StubTTS(), ShrinkingLLM([MID, SHORT]))

    data = json.loads(job.translation_json.read_text(encoding="utf-8"))
    assert data["segments"][0]["revision"] >= 1
    report = json.loads((job.tts_dir / "fit.json").read_text(encoding="utf-8"))
    assert report["segments"][0]["revision"] >= 1


def test_no_rewrite_when_segment_already_fits(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed_and_synth(job, cfg_fixture, 0, 3000, SHORT)  # ratio 0.22 -> pad
    llm = StubbornLLM(SHORT)

    fit_stage.run_with(job, cfg_fixture, StubTTS(), llm)

    assert llm.calls == 0


def test_mildly_long_segment_uses_tempo_not_rewrite(tmp_path: Path, cfg_fixture):
    """ratio <= 1.15 nén bằng atempo; gọi LLM ở đây là phí tiền và phí thời gian."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    # 5 âm tiết = 1100ms trong khe 1000ms -> ratio 1.1
    _seed_and_synth(job, cfg_fixture, 0, 1000, MID)
    llm = StubbornLLM(SHORT)

    fit_stage.run_with(job, cfg_fixture, StubTTS(), llm)

    assert llm.calls == 0
    report = json.loads((job.tts_dir / "fit.json").read_text(encoding="utf-8"))
    assert report["segments"][0]["action"] == "tempo"


def test_syllable_count_is_refreshed_after_rewrite(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed_and_synth(job, cfg_fixture, 0, 1000, LONG)

    fit_stage.run_with(job, cfg_fixture, StubTTS(), ShrinkingLLM([SHORT]))

    seg = json.loads(job.translation_json.read_text(encoding="utf-8"))["segments"][0]
    assert seg["syllables"] == 3
    assert seg["text"] == SHORT


def test_manifest_reflects_the_rewritten_durations(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed_and_synth(job, cfg_fixture, 0, 1000, LONG)

    fit_stage.run_with(job, cfg_fixture, StubTTS(), ShrinkingLLM([SHORT]))

    manifest = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["segments"][0]["actual_ms"] == 660  # 3 âm tiết x 220ms


def test_empty_rewrite_from_llm_is_rejected(tmp_path: Path, cfg_fixture):
    """Câu rỗng lọt qua sẽ thành một khoảng lặng không ai giải thích được."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed_and_synth(job, cfg_fixture, 0, 1000, LONG)

    with pytest.raises(ValueError, match="rỗng"):
        fit_stage.run_with(job, cfg_fixture, StubTTS(), StubbornLLM("   "))


def test_rate_is_never_used_to_shorten(tmp_path: Path, cfg_fixture):
    """spec §7.7: rate của CapCut không có tác dụng, fit không được trông vào nó."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed_and_synth(job, cfg_fixture, 0, 1000, LONG)
    seen = []

    class RecordingTTS(StubTTS):
        def synthesize(self, text, lang, voice, out) -> TTSResult:
            seen.append({"text": text, "voice": voice})
            return super().synthesize(text, lang, voice, out)

    fit_stage.run_with(job, cfg_fixture, RecordingTTS(), ShrinkingLLM([SHORT]))

    # chỉ đọc lại vì CHỮ đổi, không phải vì tốc độ đổi
    assert [c["text"] for c in seen] == [SHORT]
