import pytest

from reup.ass import build_ass, escape, format_time
from reup.models import Segment, Transcript


def t(*specs) -> Transcript:
    return Transcript(
        source_lang="vi",
        segments=[
            Segment(id=i, start_ms=s, end_ms=e, text=x)
            for i, (s, e, x) in enumerate(specs, start=1)
        ],
    )


@pytest.mark.parametrize(
    "ms,expected",
    [
        (0, "0:00:00.00"),
        (1500, "0:00:01.50"),
        (61230, "0:01:01.23"),
        (3661000, "1:01:01.00"),
    ],
)
def test_time_format_is_centiseconds(ms, expected):
    assert format_time(ms) == expected


def test_time_rounds_down_not_up():
    """Làm tròn lên đẩy dòng sau chồng lên dòng trước."""
    assert format_time(1999) == "0:00:01.99"


def test_negative_time_is_clamped():
    assert format_time(-50) == "0:00:00.00"


def test_newlines_become_ass_line_breaks():
    assert escape("dòng một\ndòng hai") == "dòng một\\Ndòng hai"


def test_braces_are_escaped():
    """{...} là mã lệnh của ASS — chữ thật chứa nó sẽ thành lệnh nếu không thoát."""
    assert escape("{b}đậm") == "\\{b\\}đậm"


def test_backslash_is_escaped():
    assert escape("a\\b") == "a\\\\b"


def test_header_declares_the_reference_resolution():
    out = build_ass(t((0, 1000, "a")), "Be Vietnam Pro", 64, 4)
    assert "PlayResX: 1080" in out
    assert "PlayResY: 1920" in out


def test_style_carries_font_size_and_outline():
    out = build_ass(t((0, 1000, "a")), "Be Vietnam Pro", 64, 4)
    style = next(l for l in out.splitlines() if l.startswith("Style:"))
    assert "Be Vietnam Pro" in style
    assert ",64," in style


def test_bottom_position_uses_alignment_two():
    out = build_ass(t((0, 1000, "a")), "X", 64, 4, position="bottom")
    assert next(l for l in out.splitlines() if l.startswith("Style:")).split(",")[18] == "2"


def test_top_position_uses_alignment_eight():
    out = build_ass(t((0, 1000, "a")), "X", 64, 4, position="top")
    assert next(l for l in out.splitlines() if l.startswith("Style:")).split(",")[18] == "8"


def test_one_dialogue_line_per_segment():
    out = build_ass(t((0, 1000, "một"), (1000, 2000, "hai")), "X", 64, 4)
    assert len([l for l in out.splitlines() if l.startswith("Dialogue:")]) == 2


def test_dialogue_carries_text_and_timing():
    out = build_ass(t((500, 3700, "Hôm nay trời đẹp")), "X", 64, 4)
    line = next(l for l in out.splitlines() if l.startswith("Dialogue:"))
    assert "0:00:00.50" in line
    assert "0:00:03.70" in line
    assert line.endswith("Hôm nay trời đẹp")


def test_empty_segments_are_skipped():
    """Đoạn rỗng thành một dòng Dialogue trống, libass vẫn dựng khung cho nó."""
    out = build_ass(t((0, 1000, "  "), (1000, 2000, "hai")), "X", 64, 4)
    assert len([l for l in out.splitlines() if l.startswith("Dialogue:")]) == 1


def test_empty_transcript_still_produces_valid_header():
    out = build_ass(Transcript(source_lang="vi", segments=[]), "X", 64, 4)
    assert "[Events]" in out
    assert "Dialogue:" not in out


def test_vietnamese_diacritics_survive():
    out = build_ass(t((0, 1000, "Thịt kho tàu ngon tuyệt")), "X", 64, 4)
    assert "Thịt kho tàu ngon tuyệt" in out
