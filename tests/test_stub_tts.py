# tests/test_stub_tts.py
from pathlib import Path
import pytest
from reup.adapters.stub_tts import StubTTS
from reup.media.audio import duration_ms


def test_lists_at_least_one_vietnamese_voice():
    voices = StubTTS().voices("vi")
    assert len(voices) >= 1
    assert all(v.lang == "vi" for v in voices)


def test_duration_scales_with_syllable_count(tmp_path: Path):
    tts = StubTTS(ms_per_syllable=200)
    result = tts.synthesize("một hai ba bốn năm", "vi", "stub-vi-1", tmp_path / "a.wav")
    assert result.actual_ms == 1000
    assert abs(duration_ms(result.path) - 1000) <= 100


def test_chinese_text_counts_han_characters(tmp_path: Path):
    tts = StubTTS(ms_per_syllable=100)
    result = tts.synthesize("今天教大家", "zh", "stub-zh-1", tmp_path / "a.wav")
    assert result.actual_ms == 500


def test_empty_text_still_produces_a_short_file(tmp_path: Path):
    result = StubTTS().synthesize("", "vi", "stub-vi-1", tmp_path / "a.wav")
    assert result.actual_ms > 0
    assert result.path.exists()


def test_returns_the_path_it_was_given(tmp_path: Path):
    out = tmp_path / "nested" / "seg_0001.wav"
    result = StubTTS().synthesize("xin chào", "vi", "stub-vi-1", out)
    assert result.path == out
    assert out.exists()


def test_rejects_unknown_voice(tmp_path: Path):
    with pytest.raises(ValueError, match="khong-co"):
        StubTTS().synthesize("xin chào", "vi", "khong-co", tmp_path / "a.wav")
