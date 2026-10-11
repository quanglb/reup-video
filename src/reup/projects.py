"""Dự án đặc thù: nhóm job có chung một kiểu dựng riêng.

Job thường không thuộc dự án nào. Job thuộc dự án mang khoá `project` trong
job.json; compose đọc khoá đó để đặt video vào khung nền của dự án.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).parent / "assets"


@dataclass(frozen=True)
class Project:
    key: str
    label: str
    frame: Path  # ảnh khung nền dọc, vùng đặt video tô xanh lá (chroma)


PROJECTS: dict[str, Project] = {
    "lol": Project(key="lol", label="Liên Minh", frame=ASSETS / "frames" / "lol.png"),
}


def get(key: str | None) -> Project | None:
    return PROJECTS.get(key or "")


def _is_green(p: tuple[int, int, int]) -> bool:
    r, g, b = p[:3]
    return g > 100 and g > r * 1.6 and g > b * 1.6


@lru_cache(maxsize=8)
def green_box(frame: Path) -> tuple[float, float, float, float]:
    """Vùng xanh trong ảnh khung, dạng tỉ lệ (x, y, w, h) trên cạnh ảnh.

    Dò lúc chạy thay vì ghi cứng toạ độ: đổi ảnh khung khác thì không phải sửa
    code. Một hàng/cột tính là thuộc vùng xanh khi quá nửa số điểm là xanh —
    viền mờ (vignette) và đường kẻ vàng ở mép vùng bị loại tự nhiên.
    """
    im = Image.open(frame).convert("RGB")
    w, h = im.size
    px = im.load()
    rows = [y for y in range(h) if sum(_is_green(px[x, y]) for x in range(0, w, 2)) * 2 > w / 2]
    if not rows:
        raise ValueError(f"không thấy vùng xanh trong {frame}")
    y0, y1 = rows[0], rows[-1] + 1
    cols = [
        x for x in range(w)
        if sum(_is_green(px[x, y]) for y in range(y0, y1, 2)) * 2 > (y1 - y0) / 2
    ]
    x0, x1 = cols[0], cols[-1] + 1
    # Cột sát mép ảnh bị vignette làm tối nên rơi khỏi ngưỡng; vùng đã gần chạm
    # mép thì coi như chạm luôn để không hở một vạch khung.
    if x0 <= 4:
        x0 = 0
    if w - x1 <= 4:
        x1 = w
    return x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h


def _even(v: float) -> int:
    return int(round(v / 2)) * 2


def frame_rect(frame: Path, out_w: int, out_h: int) -> tuple[int, int, int, int]:
    """Vùng đặt video (x, y, w, h) trên khung ra out_w x out_h, làm tròn số chẵn
    cho yuv420p."""
    fx, fy, fw, fh = green_box(frame)
    x, y = _even(fx * out_w), _even(fy * out_h)
    return x, y, min(out_w - x, _even(fw * out_w)), min(out_h - y, _even(fh * out_h))


def render_frame(frame: Path, out: Path, out_w: int, out_h: int) -> tuple[int, int, int, int]:
    """Ghi ảnh khung cỡ out_w x out_h với vùng đặt video trong suốt. Trả vùng đó.

    Khung được dán LÊN TRÊN video (video là đầu vào chính), để video quyết
    định fps và độ dài thành phẩm — ảnh tĩnh làm nền chính sẽ kéo fps về 25.
    """
    rect = frame_rect(frame, out_w, out_h)
    x, y, w, h = rect
    im = Image.open(frame).convert("RGBA").resize((out_w, out_h), Image.LANCZOS)
    im.paste((0, 0, 0, 0), (x, y, x + w, y + h))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return rect
