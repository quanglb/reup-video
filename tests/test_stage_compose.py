# tests/test_stage_compose.py
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


def test_filter_complex_mixes_background_in_separate_mode(cfg_fixture):
    chain = compose_stage.build_filter_complex(cfg_fixture)
    assert "volume=0.35" in chain
    assert "amix=inputs=2" in chain


def test_filter_complex_drops_original_audio_in_drop_mode(cfg_fixture):
    cfg = replace(cfg_fixture, audio=replace(cfg_fixture.audio, mode="drop_original"))
    chain = compose_stage.build_filter_complex(cfg)
    assert "amix" not in chain
    assert "volume=" not in chain


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
