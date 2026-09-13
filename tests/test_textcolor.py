"""Đoán màu chữ gốc để chữ Việt cùng màu."""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from reup.subtitle import FALLBACK_FONT, render_fitted
from reup.textcolor import from_hex, text_colors, to_hex

YELLOW = (255, 214, 0)


def frame_with_text(fill, stroke, bg=(90, 120, 150), stroke_w=6):
    img = Image.new("RGB", (1080, 400), bg)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FALLBACK_FONT), 110)
    draw.text((540, 200), "等于800克呀 Hôm", font=font, anchor="mm",
              fill=fill, stroke_width=stroke_w, stroke_fill=stroke)
    bbox = draw.textbbox((540, 200), "等于800克呀 Hôm", font=font, anchor="mm",
                         stroke_width=stroke_w)
    box = {"x": bbox[0], "y": bbox[1], "w": bbox[2] - bbox[0], "h": bbox[3] - bbox[1]}
    return img, box


def close(a, b, tol=45):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_yellow_text_with_black_outline():
    img, box = frame_with_text(YELLOW, (0, 0, 0))
    fill, outline = text_colors(img, box)
    assert close(fill, YELLOW), fill
    assert close(outline, (0, 0, 0), 60), outline


def test_white_text_without_outline_gets_a_contrasting_black_outline():
    img, box = frame_with_text((255, 255, 255), (255, 255, 255), bg=(30, 30, 30), stroke_w=0)
    fill, outline = text_colors(img, box)
    assert close(fill, (255, 255, 255))
    assert outline == (0, 0, 0)


def test_dark_text_on_light_background():
    img, box = frame_with_text((20, 20, 160), (255, 255, 255), bg=(235, 235, 225))
    fill, outline = text_colors(img, box)
    assert close(fill, (20, 20, 160), 60), fill


def test_empty_area_gives_none():
    img = Image.new("RGB", (400, 300), (100, 100, 100))
    assert text_colors(img, {"x": 100, "y": 100, "w": 200, "h": 60}) is None
    assert text_colors(img, {"x": 0, "y": 0, "w": 2, "h": 2}) is None


def test_hex_round_trip_and_bad_input():
    assert from_hex(to_hex(YELLOW)) == YELLOW
    assert from_hex("xyz") == (255, 255, 255)
    assert from_hex(None, (0, 0, 0)) == (0, 0, 0)


def test_rendered_subtitle_uses_the_given_colours(tmp_path: Path):
    out = tmp_path / "s.png"
    render_fitted("Tám trăm gam", out, FALLBACK_FONT, 600, 120, fill=YELLOW, stroke=(0, 0, 0))
    colors = {c[:3] for n, c in Image.open(out).getcolors(1 << 20) if c[3] == 255}
    assert YELLOW in colors and (0, 0, 0) in colors
    assert (255, 255, 255) not in colors


def test_window_carries_colours_from_ocr(tmp_path: Path):
    from reup.core.job import create_job
    from reup.stages import compose

    job = create_job(tmp_path / "jobs", "https://a", "zh", job_id="j1")
    job.ocr_json.write_text(json.dumps({"video_w": 1080, "video_h": 1920, "step_ms": 500, "lines": [
        {"text": "一", "start_ms": 0, "end_ms": 1000,
         "box": {"x": 100, "y": 1000, "w": 500, "h": 90, "line_h": 90,
                 "color": "#ffd600", "outline": "#000000"}},
    ]}), encoding="utf-8")
    w = compose.load_cover_windows(job)[0]
    assert (w["color"], w["outline"]) == ("#ffd600", "#000000")


def test_union_keeps_the_first_colour():
    from reup.stages.ocr import _union

    a = {"x": 0, "y": 0, "w": 10, "h": 10, "color": "#ffd600", "outline": "#000000"}
    b = {"x": 5, "y": 5, "w": 10, "h": 10, "color": "#ffffff"}
    assert _union(a, b)["color"] == "#ffd600"
    assert _union({"x": 0, "y": 0, "w": 1, "h": 1}, b)["color"] == "#ffffff"
