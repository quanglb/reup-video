from pathlib import Path

import pytest

from reup.subtitle import (
    FALLBACK_FONT,
    find_font,
    render_line,
    vertical_position,
    wrap_lines,
)

W, H = 1080, 1920


@pytest.fixture
def font():
    from PIL import ImageFont

    return ImageFont.truetype(str(FALLBACK_FONT), 64)


def test_unknown_font_falls_back_instead_of_crashing():
    """Thà chữ khác kiểu còn hơn không có phụ đề."""
    assert find_font("Font Không Tồn Tại 12345") == FALLBACK_FONT


def test_fallback_font_exists_on_this_machine():
    assert FALLBACK_FONT.exists()


def test_known_font_is_found():
    p = find_font("Arial Unicode")
    assert p.exists()
    assert "arial" in p.stem.lower()


def test_short_text_stays_on_one_line(font):
    assert wrap_lines("Hôm nay", font, 1000) == ["Hôm nay"]


def test_long_text_wraps_by_measured_width(font):
    lines = wrap_lines("Hôm nay mình dạy mọi người làm món thịt kho tàu", font, 400)
    assert len(lines) > 1
    for line in lines:
        assert font.getbbox(line)[2] <= 400


def test_wrapping_keeps_every_word(font):
    text = "một hai ba bốn năm sáu bảy tám chín mười"
    assert " ".join(wrap_lines(text, font, 300)).split() == text.split()


def test_empty_text_gives_no_lines(font):
    assert wrap_lines("", font, 500) == []
    assert wrap_lines("   ", font, 500) == []


def test_a_single_word_too_wide_is_not_dropped(font):
    """Từ dài hơn cả khung vẫn phải xuất hiện, dù tràn."""
    assert wrap_lines("Cực" * 40, font, 100) != []


def test_render_writes_a_transparent_png(tmp_path: Path):
    out = tmp_path / "s.png"
    w, h = render_line("Hôm nay trời đẹp", out, W, FALLBACK_FONT, 64, 4)

    from PIL import Image

    img = Image.open(out)
    assert img.mode == "RGBA"
    assert (w, h) == img.size
    assert w == W
    # góc trên-trái phải trong suốt, không phải nền đen
    assert img.getpixel((0, 0))[3] == 0


def test_rendered_image_actually_contains_ink(tmp_path: Path):
    out = tmp_path / "s.png"
    render_line("Hôm nay", out, W, FALLBACK_FONT, 64, 4)

    from PIL import Image

    alpha = Image.open(out).getchannel("A")
    assert alpha.getextrema()[1] > 0  # có pixel không trong suốt


def test_vietnamese_diacritics_render_wider_than_nothing(tmp_path: Path):
    """Font thiếu dấu sẽ vẽ ra ô vuông hoặc rỗng — kiểm tra có mực thật."""
    a = tmp_path / "a.png"
    render_line("ệ ọ ữ ằ ẩ", a, W, FALLBACK_FONT, 64, 4)

    from PIL import Image

    assert Image.open(a).getchannel("A").getextrema()[1] > 0


def test_taller_image_for_wrapped_text(tmp_path: Path):
    short = tmp_path / "a.png"
    long = tmp_path / "b.png"
    _, h1 = render_line("Ngắn", short, 400, FALLBACK_FONT, 64, 4)
    _, h2 = render_line("Câu này dài hơn nhiều lần và chắc chắn phải xuống dòng",
                        long, 400, FALLBACK_FONT, 64, 4)
    assert h2 > h1


def test_bottom_position_leaves_room_for_platform_ui():
    y = vertical_position(1920, 200, "bottom")
    assert y + 200 < 1920  # không dính đáy
    assert y > 1920 * 0.5  # vẫn ở nửa dưới


def test_top_position_is_near_the_top():
    assert vertical_position(1920, 200, "top") < 1920 * 0.2


def test_middle_position_is_centred():
    assert vertical_position(1920, 200, "middle") == 860


def test_position_never_goes_negative():
    """Ảnh cao hơn cả khung thì vẫn phải đặt được, không âm."""
    assert vertical_position(1920, 2500, "bottom") == 0
