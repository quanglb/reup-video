"""Registry là chỗ duy nhất biết tên engine nào ứng với class nào.

Test ở đây chỉ dựng adapter, không chạy: dựng được hay không, và lỗi có nói rõ
phải sửa knob nào trong config.toml.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from reup.adapters.capcut_stt import CapCutSTT
from reup.adapters.capcut_tts import CapCutError, CapCutTTS
from reup.adapters.cassette_llm import CassetteLLM
from reup.adapters.registry import make_asr, make_llm, make_tts
from reup.adapters.stub_tts import StubTTS
from reup.adapters.whisper_asr import WhisperASR
from reup.config import ASRConfig, LLMConfig, TTSConfig


def test_stub_tts_by_default(cfg_fixture):
    assert isinstance(make_tts(cfg_fixture), StubTTS)


def test_capcut_tts_gets_the_dir_from_config(cfg_fixture, tmp_path: Path):
    cfg = replace(cfg_fixture, tts=TTSConfig("capcut", "BV074_streaming", str(tmp_path)))
    tts = make_tts(cfg)
    assert isinstance(tts, CapCutTTS)
    assert tts.capcut_dir == tmp_path


def test_capcut_tts_without_a_dir_says_which_knob_is_missing(cfg_fixture):
    cfg = replace(cfg_fixture, tts=TTSConfig("capcut", "BV074_streaming", ""))
    with pytest.raises(CapCutError, match="tts.capcut_dir"):
        make_tts(cfg)


def test_unknown_tts_engine(cfg_fixture):
    cfg = replace(cfg_fixture, tts=TTSConfig("elevenlabs", "x", ""))
    with pytest.raises(ValueError, match="elevenlabs"):
        make_tts(cfg)


def test_whisper_asr_takes_the_model_from_the_profile(cfg_fixture):
    asr = make_asr(cfg_fixture)
    assert isinstance(asr, WhisperASR)
    assert asr.model == cfg_fixture.profile.whisper_model == "tiny"


def test_capcut_stt_reuses_the_tts_dir(cfg_fixture, tmp_path: Path):
    """Một thư mục CapCut cho cả TTS lẫn STT — không có knob riêng."""
    cfg = replace(
        cfg_fixture,
        asr=ASRConfig("capcut"),
        tts=TTSConfig("stub", "BV074_streaming", str(tmp_path)),
    )
    asr = make_asr(cfg)
    assert isinstance(asr, CapCutSTT)
    assert asr.capcut_dir == tmp_path


def test_capcut_stt_without_a_dir_says_which_knob_is_missing(cfg_fixture):
    cfg = replace(cfg_fixture, asr=ASRConfig("capcut"))
    with pytest.raises(ValueError, match="tts.capcut_dir"):
        make_asr(cfg)


def test_unknown_asr_engine(cfg_fixture):
    cfg = replace(cfg_fixture, asr=ASRConfig("deepgram"))
    with pytest.raises(ValueError, match="deepgram"):
        make_asr(cfg)


def test_cassette_llm_gets_its_path(cfg_fixture, tmp_path: Path):
    tape = tmp_path / "tape.json"
    tape.write_text("{}", encoding="utf-8")
    cfg = replace(cfg_fixture, llm=LLMConfig("cassette", "x", str(tape)))
    llm = make_llm(cfg)
    assert isinstance(llm, CassetteLLM)


def test_cassette_llm_without_a_path_says_which_knob_is_missing(cfg_fixture):
    cfg = replace(cfg_fixture, llm=LLMConfig("cassette", "x", ""))
    with pytest.raises(ValueError, match="llm.cassette"):
        make_llm(cfg)


def test_unknown_llm_provider(cfg_fixture):
    cfg = replace(cfg_fixture, llm=LLMConfig("openai", "gpt", ""))
    with pytest.raises(ValueError, match="openai"):
        make_llm(cfg)


def test_gemini_needs_a_key_not_a_registry_change(cfg_fixture, monkeypatch):
    """Thiếu key là lỗi môi trường; registry vẫn dựng được adapter."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(Exception, match="GEMINI_API_KEY"):
        make_llm(cfg_fixture)
