"""Phân loại vùng chữ — spec §7.1.

Blur nhầm watermark thì vô hại. Blur nhầm chữ trong cảnh làm hỏng hình. Bỏ sót
phụ đề thì chữ gốc còn nguyên trên video. Ba loại sai, ba mức hậu quả khác nhau.
"""
import pytest

from reup.media.ocr import TextBox, to_pixels
from reup.subdetect import (
    Cluster,
    classify,
    cluster_boxes,
    detect_regions,
    present_ranges,
)

W, H = 1080, 1920


def sub(text: str, y: int = 1500, x: int = 90, w: int = 900, h: int = 90) -> TextBox:
    return TextBox(text=text, confidence=1.0, x=x, y=y, w=w, h=h)


def mark(text: str = "@kenh") -> TextBox:
    return TextBox(text=text, confidence=1.0, x=850, y=60, w=180, h=50)


# --- đổi hệ toạ độ ----------------------------------------------------------

def test_vision_bottom_left_becomes_top_left():
    """Vision đo từ đáy lên, ffmpeg đo từ đỉnh xuống. Sai chỗ này là blur nhầm chỗ."""
    # hộp sát đáy: y=0, h=0.1 -> trong ảnh cao 1000 thì nằm ở y=900..1000
    assert to_pixels((0.0, 0.0, 1.0, 0.1), 500, 1000) == (0, 900, 500, 100)


def test_box_at_top_maps_to_y_zero():
    assert to_pixels((0.0, 0.9, 1.0, 0.1), 500, 1000) == (0, 0, 500, 100)


def test_negative_coordinates_are_clamped():
    """Vision thỉnh thoảng trả -0.0 hoặc số âm nhỏ."""
    x, y, w, h = to_pixels((-0.01, 0.35, 0.99, 0.04), 1080, 1920)
    assert x == 0
    assert y >= 0


# --- gom cụm ----------------------------------------------------------------

def test_same_position_across_frames_is_one_cluster():
    frames = [[sub("câu một")], [sub("câu hai")], [sub("câu ba")]]
    clusters = cluster_boxes(frames, H)
    assert len(clusters) == 1
    assert clusters[0].frames == {0, 1, 2}


def test_different_positions_are_separate_clusters():
    frames = [[sub("phụ đề"), mark()]]
    assert len(cluster_boxes(frames, H)) == 2


def test_cluster_grows_to_the_union_of_boxes():
    """Sub nhảy vị trí giữa video thì lấy hợp — thà blur rộng còn hơn sót chữ."""
    frames = [[sub("ngắn", x=300, w=400)], [sub("dài hơn nhiều", x=90, w=900)]]
    c = cluster_boxes(frames, H)[0]
    assert c.x == 90
    assert c.x + c.w == 990


def test_horizontal_drift_stays_in_one_cluster():
    """Câu dài ngắn khác nhau làm tâm ngang xê dịch, nhưng vẫn là một dòng sub."""
    frames = [[sub("a", x=90, w=900)], [sub("b", x=400, w=280)]]
    assert len(cluster_boxes(frames, H)) == 1


def test_vertical_drift_splits_clusters():
    """Lệch dọc nhiều là hai dòng khác nhau, không được gộp."""
    frames = [[sub("trên", y=600)], [sub("dưới", y=1500)]]
    assert len(cluster_boxes(frames, H)) == 2


# --- phân loại --------------------------------------------------------------

def test_changing_text_in_lower_centre_is_a_subtitle():
    c = Cluster(90, 1500, 900, 90, {0, 1, 2, 3}, ["một", "hai", "ba", "bốn"])
    assert classify(c, 6, W, H) == "subtitle"


def test_constant_text_in_a_corner_is_a_watermark():
    c = Cluster(850, 60, 180, 50, {0, 1, 2, 3}, ["@kenh"] * 4)
    assert classify(c, 4, W, H) == "watermark"


def test_constant_text_in_the_lower_centre_is_not_a_subtitle():
    """Bảng điểm hay tên kênh đứng giữa dưới: hiện suốt, chữ không đổi."""
    c = Cluster(90, 1500, 900, 90, set(range(10)), ["ĐANG LIVE"] * 10)
    assert classify(c, 10, W, H) != "subtitle"


def test_text_in_the_upper_half_is_scene_text():
    c = Cluster(90, 300, 900, 90, {0, 1, 2}, ["một", "hai", "ba"])
    assert classify(c, 6, W, H) == "scene"


def test_off_centre_text_is_scene_text():
    """Chữ dính mép trái không phải phụ đề — blur vào là hỏng hình."""
    c = Cluster(0, 1500, 200, 90, {0, 1, 2}, ["một", "hai", "ba"])
    assert classify(c, 6, W, H) == "scene"


def test_text_appearing_only_once_is_not_a_subtitle():
    c = Cluster(90, 1500, 900, 90, {0}, ["chớp nhoáng"])
    assert classify(c, 20, W, H) == "scene"


def test_large_constant_banner_is_not_a_watermark():
    """Watermark phải nhỏ. Vùng to mà hiện suốt là phần của cảnh."""
    c = Cluster(0, 0, W, H // 2, set(range(10)), ["NỀN"] * 10)
    assert classify(c, 10, W, H) != "watermark"


def test_zero_frames_is_rejected():
    with pytest.raises(ValueError, match="total_frames"):
        classify(Cluster(0, 0, 10, 10), 0, W, H)


# --- vùng cần blur ----------------------------------------------------------

def test_regions_include_subtitle_and_watermark_but_not_scene():
    frames = [
        [sub("một"), mark(), TextBox("CẢNH", 1.0, 0, 300, 200, 60)],
        [sub("hai"), mark(), TextBox("CẢNH", 1.0, 0, 300, 200, 60)],
        [sub("ba"), mark()],
        [sub("bốn"), mark()],
    ]
    kinds = {r.kind for r in detect_regions(frames, W, H)}
    assert kinds == {"subtitle", "watermark"}


def test_region_is_padded_outwards():
    """Viền chữ và bóng đổ thò ra ngoài hộp Vision trả về."""
    frames = [[sub("một")], [sub("hai")], [sub("ba")], [sub("bốn")]]
    r = next(r for r in detect_regions(frames, W, H, padding=8) if r.kind == "subtitle")
    assert r.x == 82  # 90 - 8
    assert r.w == 916  # 900 + 16


def test_padding_never_escapes_the_frame():
    frames = [[TextBox("x", 1.0, 0, H - 40, W, 40)] for _ in range(4)]
    for r in detect_regions(frames, W, H, padding=50):
        assert r.x >= 0 and r.y >= 0
        assert r.x + r.w <= W
        assert r.y + r.h <= H


def test_no_frames_gives_no_regions():
    assert detect_regions([], W, H) == []


def test_frames_without_text_give_no_regions():
    assert detect_regions([[], [], []], W, H) == []


# --- khoảng thời gian có phụ đề --------------------------------------------

def test_consecutive_frames_merge_into_one_range():
    frames = [[sub("một")], [sub("hai")], [sub("ba")], [sub("bốn")]]
    assert present_ranges(frames, W, H, step_ms=500) == [[0, 2000]]


def test_gap_splits_ranges():
    frames = [[sub("một")], [sub("hai")], [], [], [sub("ba")], [sub("bốn")]]
    assert present_ranges(frames, W, H, step_ms=500) == [[0, 1000], [2000, 3000]]


def test_watermark_does_not_create_ranges():
    frames = [[mark()] for _ in range(6)]
    assert present_ranges(frames, W, H, step_ms=500) == []


def test_subtitle_present_in_every_frame_is_still_a_subtitle():
    """Clip ngắn người nói liên tục có hardsub ở mọi khung.

    Bản đầu đặt trần tần suất 95% theo spec §7.1 ("hiện 40-90%") và vì thế bỏ
    sót đúng cái video thật đầu tiên đem ra thử.
    """
    c = Cluster(90, 1500, 900, 90, set(range(10)), [f"câu {i}" for i in range(10)])
    assert classify(c, 10, W, H) == "subtitle"
