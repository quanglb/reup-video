from pathlib import Path

import pytest

from reup.config import load_config


def test_loads_active_profile(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[profile]\nactive = "air-16"\n\n'
        '[profile.air-16]\nconcurrency = 1\nwhisper_model = "tiny"\n'
        'demucs_segment = 7\nencoder = "h264_videotoolbox"\n\n'
        '[profile.studio-24]\nconcurrency = 2\nwhisper_model = "big"\n'
        'demucs_segment = 12\nencoder = "h264_videotoolbox"\n\n'
        '[audio]\nmode = "separate"\nbgm_gain = 0.35\n\n'
        '[transform]\nhflip = false\nzoom = 1.0\nspeed = 1.0\n\n'
        '[subtitle]\nfont = "Be Vietnam Pro"\nsize = 64\noutline = 4\nposition = "bottom"\n\n'
        '[review]\nauto_approve_b = false\n',
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.profile_name == "air-16"
    assert cfg.profile.concurrency == 1
    assert cfg.profile.whisper_model == "tiny"
    assert cfg.audio.bgm_gain == 0.35
    assert cfg.transform.hflip is False
    assert cfg.subtitle.font == "Be Vietnam Pro"
    assert cfg.review.auto_approve_b is False


def test_rejects_unknown_active_profile(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[profile]\nactive = "khong-co"\n\n'
        '[profile.air-16]\nconcurrency = 1\nwhisper_model = "tiny"\n'
        'demucs_segment = 7\nencoder = "h264_videotoolbox"\n\n'
        '[audio]\nmode = "separate"\nbgm_gain = 0.35\n\n'
        '[transform]\nhflip = false\nzoom = 1.0\nspeed = 1.0\n\n'
        '[subtitle]\nfont = "X"\nsize = 64\noutline = 4\nposition = "bottom"\n\n'
        '[review]\nauto_approve_b = false\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="khong-co"):
        load_config(cfg_file)


def test_rejects_unknown_audio_mode(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[profile]\nactive = "air-16"\n\n'
        '[profile.air-16]\nconcurrency = 1\nwhisper_model = "tiny"\n'
        'demucs_segment = 7\nencoder = "h264_videotoolbox"\n\n'
        '[audio]\nmode = "lung-tung"\nbgm_gain = 0.35\n\n'
        '[transform]\nhflip = false\nzoom = 1.0\nspeed = 1.0\n\n'
        '[subtitle]\nfont = "X"\nsize = 64\noutline = 4\nposition = "bottom"\n\n'
        '[review]\nauto_approve_b = false\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="lung-tung"):
        load_config(cfg_file)
