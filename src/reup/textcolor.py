"""Đoán màu chữ và màu viền của phụ đề gốc từ một khung hình. Hàm thuần, chỉ Pillow.

Để chữ Việt cùng màu với chữ gốc: chữ gốc vàng viền đen thì chữ Việt cũng vàng
viền đen.

Cách đoán, không cần máy học:
1. Màu NỀN lấy từ hai dải ảnh ngay trên và ngay dưới hộp chữ (ngoài hộp nên
   không có nét chữ).
2. Điểm ảnh trong hộp khác hẳn màu nền là NÉT CHỮ (gồm cả ruột lẫn viền).
3. Co mặt nạ nét chữ vào vài điểm ảnh: phần còn lại là RUỘT chữ -> màu chữ.
   Phần bị co mất là MÉP -> màu viền.
4. Viền gần trùng màu chữ (chữ không viền) thì chọn đen/trắng tương phản, để
   chữ Việt vẫn đọc được trên mọi nền.
"""
from __future__ import annotations

from pathlib import Path

# Khác màu nền quá ngần này (khoảng cách RGB) thì coi là nét chữ.
INK_DISTANCE = 70
# Nét chữ phải chiếm ít nhất ngần này của hộp, không thì hộp là nhiễu.
MIN_INK_SHARE = 0.03
# Viền phải khác màu chữ ít nhất ngần này mới tính là có viền thật. Mép chữ
# khử răng cưa pha chữ với nền ra màu xám, cách chữ trắng chừng 90 — đó không
# phải viền.
MIN_OUTLINE_CONTRAST = 120
# Thu nhỏ hộp trước khi đếm cho nhanh. 480 chứ không 240: thu nhỏ quá thì nét
# chữ chỉ còn 1-2 điểm ảnh, không còn ruột để lấy màu.
SAMPLE_WIDTH = 480
# Gom màu theo ô cỡ ngần này mỗi kênh rồi lấy ô đông nhất.
BUCKET = 24

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _median(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    n = len(pixels)
    return tuple(sorted(p[c] for p in pixels)[n // 2] for c in range(3))


def _dominant(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    """Màu của nhóm đông nhất, KHÔNG phải trung vị.

    Trung vị bị kéo về phía các điểm ảnh mép pha màu (vàng pha đen ra vàng
    sẫm, trắng pha nền ra xám). Màu thật của chữ là màu lặp lại nhiều nhất.
    """
    groups: dict[tuple[int, int, int], list] = {}
    for p in pixels:
        groups.setdefault((p[0] // BUCKET, p[1] // BUCKET, p[2] // BUCKET), []).append(p)
    best = max(groups.values(), key=len)
    n = len(best)
    return tuple(round(sum(p[c] for p in best) / n) for c in range(3))


def _luma(c) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def to_hex(c) -> str:
    return "#{:02x}{:02x}{:02x}".format(*c)


def from_hex(s: str | None, default=WHITE) -> tuple[int, int, int]:
    s = (s or "").lstrip("#")
    if len(s) != 6:
        return default
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def text_colors(image, box: dict) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
    """(màu chữ, màu viền) của chữ trong `box` trên ảnh PIL. Không đoán được thì None."""
    from PIL import ImageFilter

    img = image.convert("RGB")
    W, H = img.size
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    if w < 4 or h < 4:
        return None
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None

    # Nền: dải cao nửa dòng ngay trên và ngay dưới hộp.
    band = max(3, h // 3)
    strips = []
    if y0 - band >= 0:
        strips.append(img.crop((x0, y0 - band, x1, y0)))
    if y1 + band <= H:
        strips.append(img.crop((x0, y1, x1, y1 + band)))
    if not strips:
        return None
    bg_pixels = [p for s in strips for p in s.resize((max(1, s.width // 4), max(1, s.height // 2))).getdata()]
    bg = _median(bg_pixels)

    crop = img.crop((x0, y0, x1, y1))
    scale = min(1.0, SAMPLE_WIDTH / crop.width)
    crop = crop.resize((max(8, round(crop.width * scale)), max(8, round(crop.height * scale))))
    pixels = list(crop.getdata())

    mask = crop.point(lambda _: 0).convert("L")
    mask.putdata([255 if _dist(p, bg) > INK_DISTANCE else 0 for p in pixels])
    ink = [p for p, m in zip(pixels, mask.getdata()) if m]
    if len(ink) < len(pixels) * MIN_INK_SHARE:
        return None

    # Phân biệt ruột và viền theo VỊ TRÍ, không theo số đông: viền đen dày
    # quanh nét chữ Trung mảnh thường chiếm nhiều điểm ảnh hơn ruột, nên "màu
    # đông nhất" lại ra màu viền.
    #
    # Viền là thứ CHẠM NỀN: điểm nét chữ nằm sát một điểm nền. Ruột là phần nét
    # chữ khác hẳn màu viền.
    core_mask = mask.filter(ImageFilter.MinFilter(3))
    edge = [p for p, m, c in zip(pixels, mask.getdata(), core_mask.getdata()) if m and not c]
    if not edge:
        edge = ink
    outline = _dominant(edge)
    inner = [p for p in ink if _dist(p, outline) >= MIN_OUTLINE_CONTRAST]
    fill = _dominant(inner) if len(inner) >= len(ink) * 0.15 else None

    # Màu "ruột" nằm GIỮA viền và nền (gần như trên đường nối hai màu) là màu
    # pha của mép khử răng cưa, không phải màu chữ: chữ trắng không viền trên
    # nền tối có mép xám, và mép xám đó khác trắng đủ xa để lọt qua bước trên.
    if fill is not None and _dist(fill, bg) + _dist(fill, outline) <= _dist(outline, bg) * 1.15:
        fill = None

    if fill is None:
        # Gần như không có gì khác màu mép: chữ không viền (mép chỉ là răng cưa
        # pha nền). Màu chữ là màu đông nhất, viền chọn đen/trắng tương phản.
        fill = _dominant(ink)
        outline = BLACK if _luma(fill) > 110 else WHITE
    return fill, outline


def colors_from_frame(path: Path, box: dict) -> dict:
    """{"color": "#rrggbb", "outline": "#rrggbb"} cho hộp trong file ảnh, hoặc {}."""
    from PIL import Image

    try:
        with Image.open(path) as im:
            found = text_colors(im, box)
    except OSError:
        return {}
    if not found:
        return {}
    return {"color": to_hex(found[0]), "outline": to_hex(found[1])}
