# tests/test_stage_compose.py
import pytest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from reup.core.job import create_job
from reup.media.ffmpeg import MediaInfo, probe
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


# --- timeout cho lệnh render cuối: max(60, duration_s * 4) -------------------

@pytest.mark.parametrize(
    "duration_ms, expected_timeout_s",
    [
        (10_000, 60.0),    # 10s * 4 = 40 < sàn -> 60s
        (30_000, 120.0),   # 30 * 4 = 120
        (5_000, 60.0),     # rất ngắn -> vẫn sàn 60s
        (100_000, 400.0),  # 100 * 4 = 400
    ],
)
def test_run_passes_timeout_computed_from_source_duration(
    tmp_path: Path, sample_video: Path, sample_wav: Path, cfg_fixture,
    duration_ms, expected_timeout_s,
):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())
    job.dub_wav.write_bytes(sample_wav.read_bytes())

    fake_info = MediaInfo(
        duration_ms=duration_ms, width=540, height=960,
        has_video=True, has_audio=True, video_bps=1_600_000,
    )

    def fake_run_ffmpeg(args, timeout_s=None):
        # thoả os.replace(tmp_mp4, ...) ngay sau lệnh render
        tmp_mp4 = job.final_mp4.with_suffix(".tmp.mp4")
        tmp_mp4.write_bytes(sample_video.read_bytes())
        return ""

    with patch("reup.stages.compose.probe", return_value=fake_info) as mock_probe, \
         patch("reup.stages.compose.run_ffmpeg", side_effect=fake_run_ffmpeg) as mock_run_ffmpeg:
        compose_stage.run(job, cfg_fixture)

    assert mock_run_ffmpeg.call_args.kwargs["timeout_s"] == expected_timeout_s


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


# --- che vùng phụ đề gốc (spec §7.2, §7.3) ----------------------------------

def test_no_regions_means_no_blur(cfg_fixture):
    chain = compose_stage.build_filter_complex(cfg_fixture, has_bgm=False)
    assert "boxblur" not in chain


def test_each_region_gets_its_own_blur_pass(cfg_fixture):
    regions = [
        {"x": 82, "y": 1155, "w": 916, "h": 100, "kind": "subtitle"},
        {"x": 850, "y": 60, "w": 180, "h": 50, "kind": "watermark"},
    ]
    chain = compose_stage.build_filter_complex(cfg_fixture, False, regions)
    assert chain.count("boxblur") == 2
    assert "crop=916:100:82:1155" in chain
    assert "crop=180:50:850:60" in chain


def test_blur_happens_before_the_transform(cfg_fixture):
    """Toạ độ vùng tính trên khung GỐC — blur sau khi zoom là blur nhầm chỗ."""
    cfg = replace(cfg_fixture, transform=replace(cfg_fixture.transform, zoom=1.05))
    regions = [{"x": 82, "y": 1155, "w": 916, "h": 100, "kind": "subtitle"}]
    chain = compose_stage.build_filter_complex(cfg, False, regions)
    assert chain.index("boxblur") < chain.index("scale=")


def test_subtitles_are_applied_after_hflip(cfg_fixture):
    """Bẫy spec §7.3: hflip sau khi dán sub sẽ lật ngược chữ tiếng Việt."""
    from reup.subtitle import Overlay

    cfg = replace(cfg_fixture, transform=replace(cfg_fixture.transform, hflip=True))
    ovs = [Overlay(Path("a.png"), 0, 1000, 0, 1500)]
    chain = compose_stage.build_filter_complex(cfg, False, None, None, ovs, 2)
    assert chain.index("hflip") < chain.index("overlay=0:1500")


def test_each_subtitle_overlay_is_time_gated(cfg_fixture):
    from reup.subtitle import Overlay

    ovs = [
        Overlay(Path("a.png"), 0, 1500, 0, 1500),
        Overlay(Path("b.png"), 1500, 3000, 0, 1500),
    ]
    chain = compose_stage.build_filter_complex(cfg_fixture, False, None, None, ovs, 2)
    assert "between(t,0.000,1.500)" in chain
    assert "between(t,1.500,3.000)" in chain


def test_overlay_inputs_are_numbered_after_the_audio_inputs(cfg_fixture):
    """Đánh sai số input là dán nhầm ảnh, hoặc ffmpeg gãy."""
    from reup.subtitle import Overlay

    ovs = [Overlay(Path("a.png"), 0, 1000, 0, 1500)]
    chain = compose_stage.build_filter_complex(cfg_fixture, True, None, None, ovs, 3)
    assert "[3:v]overlay=" in chain


def test_ass_path_wins_over_overlays_when_libass_exists(cfg_fixture):
    from reup.subtitle import Overlay

    ovs = [Overlay(Path("a.png"), 0, 1000, 0, 1500)]
    chain = compose_stage.build_filter_complex(
        cfg_fixture, False, None, "/tmp/j/sub.ass", ovs, 2
    )
    assert "ass=" in chain
    assert "overlay=0:1500" not in chain


def test_colons_in_the_subtitle_path_are_escaped(cfg_fixture):
    """Dấu : ngăn cách tham số trong filtergraph — không thoát là gãy cả chuỗi."""
    chain = compose_stage.build_filter_complex(
        cfg_fixture, False, None, "/tmp/a:b/sub.ass"
    )
    assert r"a\:b" in chain


def test_renders_video_with_blur_and_subtitles(
    tmp_path: Path, sample_video: Path, sample_wav: Path, cfg_fixture
):
    """Đường đi thật qua ffmpeg: blur + overlay + trộn audio trong một lượt."""
    import json as _json

    from reup.models import Segment, Transcript

    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())
    job.dub_wav.write_bytes(sample_wav.read_bytes())
    job.subrect_json.write_text(
        _json.dumps({"video_w": 540, "video_h": 960, "regions": [
            {"x": 40, "y": 700, "w": 460, "h": 80, "kind": "subtitle", "coverage": 0.9}
        ]}),
        encoding="utf-8",
    )
    Transcript(
        source_lang="vi",
        segments=[
            Segment(id=1, start_ms=0, end_ms=3000, text="Hôm nay trời đẹp"),
            Segment(id=2, start_ms=3000, end_ms=6000, text="Mình đi chơi nhé"),
        ],
    ).save(job.translation_json)

    compose_stage.run(job, cfg_fixture)

    info = probe(job.final_mp4)
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (540, 960)
    assert abs(info.duration_ms - 6000) <= 200


@pytest.mark.slow
def test_many_subtitle_overlays_do_not_blow_up_render_time(
    tmp_path: Path, sample_video: Path, sample_wav: Path, cfg_fixture
):
    """Task 2.2 — `build_subtitle_overlays` nối N filter `overlay` liên tiếp,
    hình dạng từng gây treo ở `build_blur_chain` (667cef7) khi là `split` lồng
    nhau. Đo thật cho thấy `overlay` KHÔNG có vấn đề tương tự: 10→50 overlay
    trên clip 6s chỉ chênh ~1.1x (xem task-2.2-report.md), không tăng theo cấp
    số nhân — nên quyết định KHÔNG đổi cách dựng overlay (không cần sheet ảnh
    hay split-batch). Test này chốt lại quan sát đó: nếu ai đó vô tình làm
    chuỗi overlay tăng nặng trở lại (hoặc ffmpeg treo, bị Task 2.1's timeout
    chặn), test sẽ đỏ.

    Ngưỡng 3x là cao hơn hẳn số đo thật (~1.1x) để chừa dư địa cho máy chậm/CI
    ồn, nhưng vẫn đủ chặt để bắt lại một hồi quy về tăng siêu tuyến tính.
    """
    import time
    from unittest.mock import patch as _patch

    from PIL import Image

    from reup.subtitle import Overlay

    png_dir = tmp_path / "subs"
    png_dir.mkdir()
    png = png_dir / "sub.png"
    Image.new("RGBA", (300, 80), (255, 255, 255, 200)).save(png)

    def make_overlays(n: int) -> list[Overlay]:
        seg_ms = 6000 // n
        return [
            Overlay(path=png, start_ms=i * seg_ms, end_ms=(i + 1) * seg_ms, x=50, y=800)
            for i in range(n)
        ]

    def run_with(n: int) -> float:
        job = create_job(tmp_path / f"jobs{n}", "https://a/1", "zh", job_id="j1")
        job.source_video.write_bytes(sample_video.read_bytes())
        job.dub_wav.write_bytes(sample_wav.read_bytes())
        with _patch.object(compose_stage, "build_overlays", return_value=make_overlays(n)):
            t0 = time.monotonic()
            compose_stage.run(job, cfg_fixture)
            return time.monotonic() - t0

    elapsed_10 = run_with(10)
    elapsed_50 = run_with(50)

    assert elapsed_50 <= elapsed_10 * 3 + 1.0, (
        f"50 overlay ({elapsed_50:.2f}s) chậm hơn quá nhiều so với 10 overlay "
        f"({elapsed_10:.2f}s) — có thể chuỗi overlay đã tăng siêu tuyến tính"
    )


def test_blur_radius_shrinks_for_short_regions():
    """yuv420p chia đôi chroma; boxblur đòi bán kính < 1/4 cạnh ngắn của vùng.

    Vùng phụ đề thường dẹt, nên hằng số 20 làm ffmpeg gãy ngay:
    "Invalid chroma_param radius value 20, must be >= 0 and < 20".
    """
    assert compose_stage.blur_radius_for(460, 80) == 19


def test_blur_radius_keeps_the_default_when_there_is_room():
    assert compose_stage.blur_radius_for(916, 200) == compose_stage.BLUR_RADIUS


def test_blur_radius_never_drops_below_one():
    assert compose_stage.blur_radius_for(20, 8) >= 1


def test_generated_blur_uses_the_clamped_radius(cfg_fixture):
    regions = [{"x": 40, "y": 700, "w": 460, "h": 80, "kind": "subtitle"}]
    chain = compose_stage.build_filter_complex(cfg_fixture, False, regions)
    assert "boxblur=19:3" in chain
