from pathlib import Path

import pytest

from reup.media.audio import (
    apply_tempo, atempo_filter, build_timeline, duration_ms, extract_audio, silence,
)
from reup.media.ffmpeg import probe


@pytest.mark.parametrize(
    "ratio,expected",
    [
        (1.0, "atempo=1.000000"),
        (1.25, "atempo=1.250000"),
        (0.5, "atempo=0.500000"),
        (2.0, "atempo=2.000000"),
    ],
)
def test_atempo_single_filter_in_range(ratio, expected):
    assert atempo_filter(ratio) == expected


def test_atempo_chains_above_two():
    assert atempo_filter(4.0) == "atempo=2.0,atempo=2.000000"


def test_atempo_chains_below_half():
    assert atempo_filter(0.25) == "atempo=0.5,atempo=0.500000"


def test_atempo_rejects_non_positive():
    with pytest.raises(ValueError):
        atempo_filter(0.0)


def test_extract_audio_makes_16k_mono(sample_video: Path, tmp_path: Path):
    out = tmp_path / "full_16k.wav"
    extract_audio(sample_video, out, sample_rate=16000, channels=1)
    assert out.exists()
    assert abs(duration_ms(out) - 6000) <= 100


def test_extract_audio_makes_48k_stereo(sample_video: Path, tmp_path: Path):
    out = tmp_path / "full_48k.wav"
    extract_audio(sample_video, out, sample_rate=48000, channels=2)
    info = probe(out)
    assert info.has_audio is True
    assert abs(info.duration_ms - 6000) <= 100


def test_silence_has_requested_duration(tmp_path: Path):
    out = tmp_path / "sil.wav"
    silence(out, ms=1500)
    assert abs(duration_ms(out) - 1500) <= 100


def test_apply_tempo_shortens_audio(sample_wav: Path, tmp_path: Path):
    out = tmp_path / "fast.wav"
    apply_tempo(sample_wav, out, 2.0)
    assert abs(duration_ms(out) - 1000) <= 100


def test_apply_tempo_ratio_one_keeps_duration(sample_wav: Path, tmp_path: Path):
    out = tmp_path / "same.wav"
    apply_tempo(sample_wav, out, 1.0)
    assert abs(duration_ms(out) - 2000) <= 100


def test_build_timeline_has_requested_total_duration(sample_wav: Path, tmp_path: Path):
    out = tmp_path / "dub.wav"
    build_timeline([(0, sample_wav), (3000, sample_wav)], total_ms=6000, out=out)
    assert abs(duration_ms(out) - 6000) <= 100


def test_build_timeline_with_no_placements_is_pure_silence(tmp_path: Path):
    out = tmp_path / "dub.wav"
    build_timeline([], total_ms=4000, out=out)
    assert abs(duration_ms(out) - 4000) <= 100
