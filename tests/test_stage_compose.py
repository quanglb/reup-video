# tests/test_stage_compose.py
import pytest
from dataclasses import replace
from pathlib import Path
from reup.core.job import create_job
from reup.media.ffmpeg import probe
from reup.stages import compose as compose_stage


def test_transform_is_null_when_everything_off(cfg_fixture):
    assert compose_stage.build_transform(cfg_fixture.transform) == "null"


def test_transform_includes_hflip(cfg_fixture):
    t = replace(cfg_fixture.transform, hflip=True)
    assert compose_stage.build_transform(t) == "hflip"


def test_transform_zoom_scales_then_crops(cfg_fixture):
    t = replace(cfg_fixture.transform, zoom=1.05)
    result = compose_stage.build_transform(t)
    assert "scale=" in result
    assert "crop=" in result


def test_transform_speed_uses_setpts(cfg_fixture):
    t = replace(cfg_fixture.transform, speed=1.02)
    assert "setpts=" in compose_stage.build_transform(t)


def test_transform_combines_in_fixed_order(cfg_fixture):
    t = replace(cfg_fixture.transform, hflip=True, zoom=1.05, speed=1.02)
    result = compose_stage.build_transform(t)
    assert result.index("scale=") < result.index("hflip") < result.index("setpts=")


def test_filter_complex_mixes_separated_bgm(cfg_fixture):
    chain = compose_stage.build_filter_complex(cfg_fixture, has_bgm=True)
    assert "volume=0.35" in chain
    assert "amix=inputs=2" in chain


def test_bgm_comes_from_input_two_not_the_source(cfg_fixture):
    """Audio gốc còn nguyên giọng người nói — trộn nó vào là hai giọng chồng nhau."""
    chain = compose_stage.build_filter_complex(cfg_fixture, has_bgm=True)
    assert "[2:a]volume=" in chain
    assert "[0:a]" not in chain


def test_filter_complex_drops_original_audio_in_drop_mode(cfg_fixture):
    cfg = replace(cfg_fixture, audio=replace(cfg_fixture.audio, mode="drop_original"))
    chain = compose_stage.build_filter_complex(cfg, has_bgm=False)
    assert "amix" not in chain
    assert "volume=" not in chain


def test_missing_bgm_falls_back_to_dub_only(cfg_fixture):
    """Chưa chạy separate thì chỉ có giọng lồng — không được trộn nhầm audio gốc."""
    chain = compose_stage.build_filter_complex(cfg_fixture, has_bgm=False)
    assert "amix" not in chain
    assert "[0:a]" not in chain


def test_renders_playable_video(tmp_path: Path, sample_video: Path, sample_wav: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())
    job.dub_wav.write_bytes(sample_wav.read_bytes())

    compose_stage.run(job, cfg_fixture)

    info = probe(job.final_mp4)
    assert info.has_video is True
    assert info.has_audio is True
    assert info.width == 540
    assert info.height == 960
    assert abs(info.duration_ms - 6000) <= 200


def test_renders_with_transforms_on(tmp_path: Path, sample_video: Path, sample_wav: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())
    job.dub_wav.write_bytes(sample_wav.read_bytes())
    cfg = replace(cfg_fixture, transform=replace(cfg_fixture.transform, hflip=True, zoom=1.05))

    compose_stage.run(job, cfg)

    info = probe(job.final_mp4)
    assert info.width == 540
    assert info.height == 960


def test_spec_declares_its_artifacts():
    assert compose_stage.SPEC.name == "compose"
    assert set(compose_stage.SPEC.produces) == {"render/final.mp4"}


# --- chọn bitrate: nguồn thấp thì đừng phình file ra vô ích -------------------

def test_low_bitrate_source_gets_headroom_not_the_ceiling():
    # Short thật của YouTube ~1.6 Mbps: 1.6x = 2.56M, trên sàn 2.5M
    assert compose_stage.pick_bitrate(1_600_000, 8_000_000) == 2_560_000


def test_high_bitrate_source_is_capped_at_ceiling():
    assert compose_stage.pick_bitrate(20_000_000, 8_000_000) == 8_000_000


def test_very_low_bitrate_source_lifts_to_floor():
    assert compose_stage.pick_bitrate(400_000, 8_000_000) == compose_stage.FLOOR_BPS


def test_unknown_source_bitrate_falls_back_to_ceiling():
    assert compose_stage.pick_bitrate(0, 8_000_000) == 8_000_000


def test_ceiling_below_floor_still_wins():
    """Trần do người dùng đặt là trần thật — sàn không được vượt qua nó."""
    assert compose_stage.pick_bitrate(1_600_000, 2_000_000) == 2_000_000


def test_ceiling_must_be_positive():
    with pytest.raises(ValueError, match="ceiling_bps"):
        compose_stage.pick_bitrate(1_600_000, 0)


def test_render_respects_bitrate_ceiling_from_profile(
    tmp_path: Path, sample_video: Path, sample_wav: Path, cfg_fixture
):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())
    job.dub_wav.write_bytes(sample_wav.read_bytes())
    cfg = replace(
        cfg_fixture, profile=replace(cfg_fixture.profile, video_bitrate="2M")
    )

    compose_stage.run(job, cfg)

    out = probe(job.final_mp4)
    assert out.has_video is True
    # trần 2M: file ra không được vượt xa mức đó (cho 20% dao động của encoder)
    assert out.video_bps <= 2_400_000
