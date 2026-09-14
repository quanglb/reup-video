# tests/test_config.py
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


@pytest.mark.parametrize(
    "text,expected",
    [
        ("8M", 8_000_000),
        ("12m", 12_000_000),
        ("2500k", 2_500_000),
        ("800K", 800_000),
        ("800000", 800_000),
        ("1.5M", 1_500_000),
    ],
)
def test_parse_bitrate_accepts_ffmpeg_spellings(text, expected):
    from reup.config import parse_bitrate

    assert parse_bitrate(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "tam-mega", "0", "-5M"])
def test_parse_bitrate_rejects_junk(text):
    from reup.config import parse_bitrate

    with pytest.raises(ValueError):
        parse_bitrate(text)


def _write_cfg(path: Path, extra_profile_line: str = "") -> Path:
    path.write_text(
        '[profile]\nactive = "air-16"\n\n'
        '[profile.air-16]\nconcurrency = 1\nwhisper_model = "tiny"\n'
        f'demucs_segment = 7\nencoder = "h264_videotoolbox"\n{extra_profile_line}\n\n'
        '[audio]\nmode = "separate"\nbgm_gain = 0.35\n\n'
        '[transform]\nhflip = false\nzoom = 1.0\nspeed = 1.0\n\n'
        '[subtitle]\nfont = "X"\nsize = 64\noutline = 4\nposition = "bottom"\n\n'
        '[review]\nauto_approve_b = false\n',
        encoding="utf-8",
    )
    return path


def test_video_bitrate_defaults_when_absent(tmp_path: Path):
    cfg = load_config(_write_cfg(tmp_path / "config.toml"))
    assert cfg.profile.video_bitrate == "8M"


def test_video_bitrate_is_read_from_profile(tmp_path: Path):
    cfg = load_config(
        _write_cfg(tmp_path / "config.toml", 'video_bitrate = "12M"')
    )
    assert cfg.profile.video_bitrate == "12M"


def test_bad_video_bitrate_fails_at_load_time(tmp_path: Path):
    with pytest.raises(ValueError, match="tam-mega"):
        load_config(_write_cfg(tmp_path / "config.toml", 'video_bitrate = "tam-mega"'))


def test_shipped_config_is_loadable():
    """config.toml của dự án phải luôn đọc được — nó là mặc định khi chạy CLI."""
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.toml")
    assert cfg.profile_name == "air-16"
    assert cfg.profile.video_bitrate == "8M"


def test_prefer_h264_defaults_to_false(tmp_path: Path):
    """Cả hai máy trong spec đều là M4, có hardware AV1 decode — mặc định không cần."""
    cfg = load_config(_write_cfg(tmp_path / "config.toml"))
    assert cfg.profile.prefer_h264 is False


def test_prefer_h264_can_be_turned_on(tmp_path: Path):
    cfg = load_config(_write_cfg(tmp_path / "config.toml", "prefer_h264 = true"))
    assert cfg.profile.prefer_h264 is True


# --- LLM theo từng stage ----------------------------------------------------

def _write(tmp_path, llm_block: str):
    base = (Path(__file__).resolve().parents[1] / "config.toml").read_text(encoding="utf-8")
    head = base.split("[llm]")[0]
    p = tmp_path / "config.toml"
    p.write_text(head + llm_block, encoding="utf-8")
    return p


def test_a_stage_without_its_own_table_uses_the_shared_one(tmp_path):
    cfg = load_config(_write(tmp_path, '[llm]\nprovider = "gemini"\nmodel = "m1"\n'))
    for role in ("reconcile", "translate", "fit", "export", "pronounce"):
        assert cfg.llm_for(role).provider == "gemini"
        assert cfg.llm_for(role).model == "m1"


def test_a_stage_table_overrides_only_what_it_names(tmp_path):
    """Bố cục thật: Gemini lo translate, ba việc nhẹ đẩy sang model local."""
    cfg = load_config(_write(tmp_path, '''[llm]
provider  = "gemini"
model     = "gemini-3.6-flash"
timeout_s = 123.0

[llm.fit]
provider = "ollama"
model    = "qwen3:8b"

[llm.reconcile]
provider = "ollama"
model    = "qwen3:8b"

[llm.export]
provider = "ollama"
model    = "qwen3:8b"
'''))
    assert cfg.llm_for("translate").provider == "gemini"
    assert cfg.llm_for("fit").provider == "ollama"
    assert cfg.llm_for("fit").model == "qwen3:8b"
    # khoá không khai thì thừa kế từ [llm] chung
    assert cfg.llm_for("fit").timeout_s == 123.0


def test_a_typo_in_the_stage_name_is_rejected(tmp_path):
    """Gõ nhầm mà im lặng thì stage đó vẫn chạy model đắt tiền."""
    with pytest.raises(ValueError, match=r"bảng con lạ.*translat"):
        load_config(_write(tmp_path, '[llm]\nprovider = "gemini"\n\n[llm.translat]\nprovider = "ollama"\n'))


def test_a_bad_provider_in_a_stage_table_names_that_stage(tmp_path):
    with pytest.raises(ValueError, match="llm.fit.provider"):
        load_config(_write(tmp_path, '[llm]\nprovider = "gemini"\n\n[llm.fit]\nprovider = "khong-co"\n'))


def test_an_unknown_role_is_rejected_at_lookup(tmp_path):
    cfg = load_config(_write(tmp_path, '[llm]\nprovider = "gemini"\n'))
    with pytest.raises(ValueError, match="không có vai LLM"):
        cfg.llm_for("compose")
