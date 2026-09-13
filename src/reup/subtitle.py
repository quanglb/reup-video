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
# Font lùi về khi không thấy font trong config. Arial Bold chứ không Arial
# Unicode: bản Unicode chỉ có nét thường, trên video trông mảnh và khó đọc. Cả
# hai đều đủ dấu tiếng Việt (Arial Black thì KHÔNG — thiếu ệ ơ ư...).
_ARIAL_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
_ARIAL_UNICODE = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
FALLBACK_FONT = _ARIAL_BOLD if _ARIAL_BOLD.exists() else _ARIAL_UNICODE

# Khoảng cách dòng so với cỡ chữ, và cỡ nhỏ nhất còn đọc được trên điện thoại.
LINE_RATIO = 1.18
MIN_SIZE = 26
MAX_LINES = 2

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


def fit_font_size(
    text: str,
    font_path: Path,
    box_w: int,
    box_h: int,
    max_lines: int = MAX_LINES,
    min_size: int = MIN_SIZE,
    max_size: int | None = None,
) -> tuple[int, list[str]]:
    """Cỡ chữ LỚN NHẤT để câu nằm gọn trong khung box_w x box_h.

    Tìm nhị phân trên cỡ chữ: mỗi cỡ ngắt dòng theo bề rộng thật, rồi kiểm tổng
    chiều cao. Câu Việt thường dài hơn câu Trung cùng nghĩa, nên cho phép xuống
    tối đa `max_lines` dòng trong cùng khung thay vì thu chữ bé tí trên một dòng.

    Không cỡ nào vừa (khung quá hẹp) thì dùng `min_size` và chấp nhận tràn: chữ
    đọc được quan trọng hơn khít khung.
    """
    from PIL import ImageFont

    def lines_at(size: int) -> list[str] | None:
        font = ImageFont.truetype(str(font_path), size)
        lines = wrap_lines(text, font, box_w)
        if not lines or len(lines) > max_lines:
            return None
        if any(font.getbbox(line)[2] > box_w for line in lines):
            return None
        if len(lines) * size * LINE_RATIO > box_h:
            return None
        return lines

    hi = int(box_h / LINE_RATIO)
    if max_size:
        # Trần theo chiều cao một dòng chữ gốc: khung hai dòng không có nghĩa là
        # một câu ngắn được phép to gấp đôi chữ gốc.
        hi = min(hi, max_size)
    lo, hi = min_size, max(min_size, hi)
    best: tuple[int, list[str]] | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        found = lines_at(mid)
        if found is not None:
            best = (mid, found)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is not None:
        return best
    font = ImageFont.truetype(str(font_path), min_size)
    return min_size, wrap_lines(text, font, box_w) or [text]


def render_fitted(
    text: str,
    out: Path,
    font_path: Path,
    box_w: int,
    box_h: int,
    outline_ratio: float = 0.07,
    max_size: int | None = None,
    fill: tuple[int, int, int] | None = None,
    stroke: tuple[int, int, int] | None = None,
) -> tuple[int, int, int]:
    """Vẽ câu với cỡ chữ vừa khung. Trả (rộng, cao, cỡ chữ) của ảnh.

    Ảnh chỉ rộng bằng chữ (cộng lề viền), để người gọi đặt tâm ảnh vào tâm khung.
    Viền tỉ lệ theo cỡ chữ: viền cố định 4px thì chữ to trông mảnh, chữ nhỏ
    trông nhoè.
    """
    from PIL import Image, ImageDraw, ImageFont

    size, lines = fit_font_size(text, font_path, box_w, box_h, max_size=max_size)
    font = ImageFont.truetype(str(font_path), size)
    outline = max(2, round(size * outline_ratio))
    line_h = round(size * LINE_RATIO)
    # Lề trên dưới chừa cho dấu chồng (Ấ, Ỗ) vượt quá dòng và cho viền.
    pad = outline * 2 + round(size * 0.2)
    text_w = max(font.getbbox(line)[2] for line in lines)
    width = text_w + pad * 2
    height = line_h * len(lines) + pad * 2

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text(
            (width / 2, pad + i * line_h + line_h / 2),
            line,
            font=font,
            anchor="mm",  # neo giữa-giữa của ô dòng
            stroke_width=outline,
            # Màu lấy theo chữ gốc nếu đoán được, không thì trắng viền đen.
            fill=(*fill, 255) if fill else TEXT_COLOR,
            stroke_fill=(*stroke, 255) if stroke else OUTLINE_COLOR,
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return width, height, size


def place_in_box(
    image_w: int, image_h: int, box: dict, video_w: int, video_h: int
) -> tuple[int, int]:
    """Đặt tâm ảnh vào tâm khung, không cho ảnh thò ra ngoài khung hình."""
    x = round(box["x"] + box["w"] / 2 - image_w / 2)
    y = round(box["y"] + box["h"] / 2 - image_h / 2)
    return max(0, min(video_w - image_w, x)), max(0, min(video_h - image_h, y))
