"""Bọc Apple Vision qua ocrmac. Hàm thuần — nhận Path ảnh, trả hộp chữ.

Vision trả toạ độ chuẩn hoá 0..1 với **gốc ở góc dưới-trái**, còn ffmpeg và mọi
thứ khác trong dự án này dùng gốc **trên-trái** tính bằng pixel. Chỗ đổi hệ toạ
độ nằm gọn trong `to_pixels` để chỉ sai một lần thì sai ở một chỗ.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TextBox:
    """Một vùng chữ, toạ độ pixel, gốc trên-trái."""

    text: str
    confidence: float
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> int:
        return self.w * self.h


def to_pixels(
    box: tuple[float, float, float, float], width: int, height: int
) -> tuple[int, int, int, int]:
    """(x, y, w, h) chuẩn hoá gốc dưới-trái  ->  (x, y, w, h) pixel gốc trên-trái."""
    nx, ny, nw, nh = box
    x = round(max(0.0, nx) * width)
    w = round(min(1.0, nw) * width)
    h = round(min(1.0, nh) * height)
    # Lật trục dọc: Vision đo từ đáy lên, ffmpeg đo từ đỉnh xuống.
    y = round((1.0 - max(0.0, ny) - nh) * height)
    return x, max(0, y), w, h


def recognize(
    image: Path, width: int, height: int, languages: list[str] | None = None
) -> list[TextBox]:
    from ocrmac import ocrmac

    prefs = languages or ["en-US"]
    raw = ocrmac.OCR(str(image), language_preference=prefs).recognize()

    boxes = []
    for text, confidence, box in raw:
        clean = (text or "").strip()
        if not clean:
            continue
        x, y, w, h = to_pixels(tuple(box), width, height)
        if w <= 0 or h <= 0:
            continue
        boxes.append(
            TextBox(text=clean, confidence=float(confidence), x=x, y=y, w=w, h=h)
        )
    return boxes
