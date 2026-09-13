"""Che chữ gốc theo từng câu bằng lớp trắng, và chữ Việt co giãn vừa khung."""
import json
from pathlib import Path

from reup.core.job import create_job
from reup.stages import compose as compose_stage
from reup.stages.ocr import group_lines
from reup.subtitle import FALLBACK_FONT, fit_font_size, place_in_box, render_fitted


# --- OCR mang theo hộp chữ ------------------------------------------------------

def test_grouped_line_keeps_the_union_of_its_boxes():
    boxes = [
        {"x": 300, "y": 1200, "w": 400, "h": 60},
        {"x": 280, "y": 1205, "w": 380, "h": 62},
        None,
        {"x": 100, "y": 1000, "w": 200, "h": 50},
    ]
    lines = group_lines(["你好", "你好", "", "再见"], 500, boxes)
    assert lines[0]["box"] == {"x": 280, "y": 1200, "w": 420, "h": 67}
    assert lines[1]["box"] == {"x": 100, "y": 1000, "w": 200, "h": 50}


def test_group_lines_still_works_without_boxes():
    assert "box" not in group_lines(["a", "a"], 500)[0]


# --- cỡ chữ vừa khung ---------------------------------------------------------------

def test_short_text_grows_to_fill_the_box_height():
    size, lines = fit_font_size("Xin chào", FALLBACK_FONT, 800, 120)
    assert lines == ["Xin chào"]
    assert 90 <= size <= 120 / 1.18


def test_long_text_wraps_to_two_lines_and_shrinks():
    text = "Không phẩy tám ký bằng bao nhiêu gam vậy mọi người ơi"
    size, lines = fit_font_size(text, FALLBACK_FONT, 480, 160)
    assert len(lines) == 2
    assert len(lines) * size * 1.18 <= 160


def test_bigger_box_gives_bigger_text():
    text = "Tám trăm gam nè"
    small, _ = fit_font_size(text, FALLBACK_FONT, 400, 80)
    big, _ = fit_font_size(text, FALLBACK_FONT, 800, 160)
    assert big > small


def test_impossible_box_falls_back_to_the_minimum_size():
    size, lines = fit_font_size("một câu rất là dài không thể nào vừa", FALLBACK_FONT, 60, 20)
    assert size == 26 and lines


def test_rendered_text_is_no_wider_than_the_box_plus_outline(tmp_path: Path):
    w, h, size = render_fitted("Tám trăm gam nè", tmp_path / "a.png", FALLBACK_FONT, 500, 100)
    assert w <= 500 + 2 * (2 * max(2, round(size * 0.07)) + round(size * 0.2))
    assert (tmp_path / "a.png").exists()


def test_image_is_centred_on_the_box_and_clamped():
    assert place_in_box(200, 100, {"x": 400, "y": 1000, "w": 300, "h": 80}, 1080, 1920) == (450, 990)
    assert place_in_box(400, 100, {"x": 0, "y": 1880, "w": 100, "h": 40}, 1080, 1920) == (0, 1820)


# --- khung che theo từng câu ---------------------------------------------------------

def seed(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a", "zh", job_id="j1")
    job.ocr_json.write_text(json.dumps({
        "video_w": 1080, "video_h": 1920, "step_ms": 500,
        "lines": [
            {"text": "一", "start_ms": 0, "end_ms": 2000, "box": {"x": 300, "y": 1200, "w": 400, "h": 60}},
            {"text": "二", "start_ms": 3000, "end_ms": 4000, "box": {"x": 250, "y": 1000, "w": 600, "h": 80}},
            {"text": "cũ không box", "start_ms": 5000, "end_ms": 6000},
        ],
    }), encoding="utf-8")
    job.subrect_json.write_text(json.dumps({"regions": [
        {"x": 200, "y": 950, "w": 700, "h": 400, "kind": "subtitle"},
        {"x": 900, "y": 40, "w": 150, "h": 60, "kind": "watermark"},
    ]}), encoding="utf-8")
    return job


def test_each_ocr_line_gets_its_own_padded_timed_window(tmp_path):
    windows = compose_stage.load_cover_windows(seed(tmp_path))
    assert len(windows) == 2
    first = windows[0]
    assert (first["start_ms"], first["end_ms"]) == (0, 2250)
    assert first["x"] < 300 and first["y"] < 1200
    assert first["w"] > 400 and first["h"] > 60


def test_timed_windows_replace_the_static_subtitle_but_keep_the_watermark(tmp_path):
    regions = compose_stage.load_blur_regions(seed(tmp_path))
    kinds = [(r["kind"], "start_ms" in r) for r in regions]
    assert kinds == [("subtitle", True), ("subtitle", True), ("watermark", False)]


def test_old_ocr_without_boxes_falls_back_to_static_regions(tmp_path):
    job = seed(tmp_path)
    job.ocr_json.write_text(json.dumps({"lines": [{"text": "a", "start_ms": 0, "end_ms": 500}]}),
                            encoding="utf-8")
    regions = compose_stage.load_blur_regions(job)
    assert [r["kind"] for r in regions] == ["subtitle", "watermark"]


def test_cover_is_blurred_then_whitened_and_time_gated():
    chain, _ = compose_stage.build_blur_chain([
        {"x": 10, "y": 20, "w": 300, "h": 90, "kind": "subtitle", "start_ms": 1000, "end_ms": 2500},
        {"x": 900, "y": 40, "w": 150, "h": 60, "kind": "watermark"},
    ])
    assert chain.count("boxblur") == 2
    assert chain.count("color=white@0.1:t=fill") == 2
    assert "overlay=10:20:enable='between(t,1.000,2.500)'" in chain
    assert "overlay=900:40[" in chain  # watermark che suốt
    assert chain.index("boxblur") < chain.index("drawbox")


def test_vietnamese_line_uses_the_window_it_overlaps_most():
    windows = [
        {"x": 0, "y": 0, "w": 10, "h": 10, "start_ms": 0, "end_ms": 2250},
        {"x": 0, "y": 0, "w": 20, "h": 20, "start_ms": 2750, "end_ms": 4250},
    ]
    assert compose_stage.window_for(2000, 4000, windows) is windows[1]
    assert compose_stage.window_for(4500, 5000, windows) is windows[1]
    assert compose_stage.window_for(9000, 9500, windows) is None


# --- lọc chữ phụ và trần cỡ chữ ------------------------------------------------------

def test_small_sign_text_inside_the_subtitle_zone_is_dropped():
    from reup.media.ocr import TextBox
    from reup.stages.ocr import main_text_boxes

    sub = TextBox("等于800克呀", 1.0, 296, 985, 488, 98)
    sign1 = TextBox("豆", 0.3, 357, 1281, 25, 20)
    sign2 = TextBox("细", 0.5, 399, 1278, 36, 31)
    assert main_text_boxes([sign1, sub, sign2]) == [sub]
    assert main_text_boxes([]) == []


def test_union_keeps_the_tallest_line_height():
    from reup.stages.ocr import _union

    merged = _union({"x": 0, "y": 0, "w": 10, "h": 10, "line_h": 90},
                    {"x": 5, "y": 5, "w": 10, "h": 10, "line_h": 120})
    assert merged["line_h"] == 120


def test_short_text_in_a_tall_box_is_capped_at_the_original_line_height(tmp_path):
    from reup.config import load_config

    cfg = load_config(Path(__file__).resolve().parents[1] / "config.toml")
    box = {"x": 0, "y": 0, "w": 800, "h": 360, "line_h": 100}
    cap = compose_stage._max_size(box, cfg)
    assert cap == round(100 / 0.9)
    size, _ = fit_font_size("Cậu trước.", FALLBACK_FONT, box["w"], box["h"], max_size=cap)
    assert size <= cap
