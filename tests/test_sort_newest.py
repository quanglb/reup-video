"""Sắp kết quả quét theo ngày đăng: mới nhất trước, thiếu ngày xếp cuối."""
from types import SimpleNamespace

from reup.adapters.crawl import order_and_slice


def c(vid: str, date: str):
    return SimpleNamespace(video_id=vid, published_at=date, like_count=0,
                           view_count=0, duration_ms=0)


def test_newest_first_and_undated_last():
    found = [c("cu", "20240101"), c("khong", ""), c("moi", "2025-09-12"), c("giua", "20250101")]
    assert [x.video_id for x in order_and_slice(found, "newest", 1, 10)] == [
        "moi", "giua", "cu", "khong"
    ]
