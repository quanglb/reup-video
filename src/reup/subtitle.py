"""Vẽ phụ đề thành ảnh PNG trong suốt để overlay — hàm thuần, không đụng ffmpeg.

**Vì sao không dùng `ass` của libass như spec §7.2 định:** ffmpeg cài từ
Homebrew (công thức homebrew/core hiện tại) build **không có libass**, cũng
không có freetype hay fontconfig — nên cả ba filter `ass`, `subtitles` và
`drawtext` đều không tồn tại. Nâng cấp cũng vô ích vì công thức đã bỏ hẳn
libass khỏi danh sách phụ thuộc.

Vẽ bằng Pillow rồi `overlay` thì chạy với **mọi** bản ffmpeg. `ass.py` vẫn được
giữ và vẫn có test: máy nào có libass thì dùng đường đó cho chữ đẹp hơn.

Chọn font theo tên trong config, dò trong các thư mục font của macOS. Không
tìm thấy thì lùi về một font Unicode chắc chắn có dấu tiếng Việt, chứ không
gãy — thà chữ khác kiểu còn hơn không có phụ đề.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FONT_DIRS = (
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
)
# Chắc chắn có đủ dấu tiếng Việt trên mọi máy macOS.
FALLBACK_FONT = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")

TEXT_COLOR = (255, 255, 255, 255)
OUTLINE_COLOR = (0, 0, 0, 255)
# Chừa lề hai bên để chữ không dính mép khung.
SIDE_MARGIN_RATIO = 0.06


@dataclass(frozen=True)
class Overlay:
    """Một ảnh phụ đề và khoảng thời gian nó hiện."""

    path: Path
    start_ms: int
    end_ms: int
    x: int
    y: int


def find_font(name: str) -> Path:
    """Tìm file font theo tên hiển thị. Ưu tiên bản Bold cho dễ đọc trên video."""
    wanted = name.lower().replace(" ", "")
    candidates: list[Path] = []
    for d in FONT_DIRS:
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            stem = p.stem.lower().replace(" ", "").replace("-", "")
            if wanted and wanted in stem:
                candidates.append(p)
    if candidates:
        bold = [p for p in candidates if "bold" in p.stem.lower()]
        return (bold or candidates)[0]
    return FALLBACK_FONT


def wrap_lines(text: str, font, max_width: int) -> list[str]:
    """Ngắt dòng theo chiều rộng thật của chữ, không theo số ký tự."""
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if font.getbbox(trial)[2] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_line(
    text: str,
    out: Path,
    video_w: int,
    font_path: Path,
    size: int,
    outline: int,
) -> tuple[int, int]:
    """Vẽ một câu ra PNG trong suốt. Trả (rộng, cao) của ảnh."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(font_path), size)
    max_width = int(video_w * (1 - SIDE_MARGIN_RATIO * 2))
    lines = wrap_lines(text, font, max_width) or [""]

    line_h = int(size * 1.35)
    pad = outline * 3  # chỗ cho viền và bóng khỏi bị cắt
    height = line_h * len(lines) + pad * 2

    img = Image.new("RGBA", (video_w, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        y = pad + i * line_h
        draw.text(
            (video_w / 2, y),
            line,
            font=font,
            fill=TEXT_COLOR,
            anchor="ma",  # neo giữa-trên
            stroke_width=outline,
            stroke_fill=OUTLINE_COLOR,
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return img.width, img.height


def vertical_position(
    video_h: int, image_h: int, position: str, cover: dict | None = None
) -> int:
    """Chỗ đặt phụ đề Việt.

    Có vùng phụ đề gốc thì **đè lên chính nó** (spec §7.2): dải blur khi đó bị
    che gần kín, nên nó không cần đẹp. Đặt sub ở đáy trong khi blur ở giữa
    khung để lộ một vệt mờ chình ình mà chẳng được gì.

    Không có vùng nào thì lùi về vị trí trong config.
    """
    if cover:
        centre = cover["y"] + cover["h"] / 2
        return max(0, min(video_h - image_h, round(centre - image_h / 2)))
    if position == "top":
        return int(video_h * 0.08)
    if position == "middle":
        return (video_h - image_h) // 2
    return max(0, video_h - image_h - int(video_h * 0.12))
