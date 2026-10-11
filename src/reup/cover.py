"""Chọn cách che chữ gốc sao cho KHÔNG để lại mảng mờ.

Mảng blur hình chữ nhật là dấu hiệu "che logo/watermark" mà TikTok nêu đích danh
trong nhãn "nội dung không phải nguyên tác". Hai cách che thay thế:

- `flat`: chữ gốc nằm trên nền đồng màu (dải đen letterbox — gần như mọi video
  Douyin dọc). Tô đúng màu nền: chữ biến mất, không còn dấu vết gì.
- `plate`: nền có hình. Vẫn blur để xoá nét chữ, nhưng phủ lên một tấm nền tối
  gần như đặc — trông như hộp caption được thiết kế, không phải kính mờ.

Đo "đồng màu" trên dải viền mỏng ngay ngoài vùng che, ở vài khung trong khoảng
thời gian vùng đó hiện.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

FLAT = "flat"
PLATE = "plate"

RING_PX = 24  # rộng để bắt cả tay/vật đang tiến sát vùng giữa hai lần lấy mẫu (2 khung/giây)
# Độ lệch chuẩn độ sáng của dải viền dưới mức này thì coi là nền phẳng. Letterbox
# đen nén H.264 đo ra 0-1; mép một bàn tay vừa chạm dải viền đã ra 3.5-6, nền có
# hình ra 20+. Ngờ thì rơi về plate — vẫn an toàn, chỉ kém đẹp hơn.
FLAT_LUMA_STD = 3.0
# Màu nền giữa các khung lệch quá mức này (vd. cảnh chuyển sáng/tối) thì không
# dùng một màu tô cố định được.
FLAT_FRAME_DRIFT = 14.0
SAMPLES = 5

PLATE_COLOR = "0x111111"
PLATE_OPACITY = 0.82


@lru_cache(maxsize=12)
def _load(path: str):
    from PIL import Image

    with Image.open(path) as im:
        return im.convert("RGB")


_SENTINEL = (1, 2, 3)


def ring_pixels(img, box: dict, ring: int = RING_PX,
                exclude: list[dict] | None = None) -> list[tuple[int, int, int]]:
    """Điểm ảnh trên dải viền ngay ngoài hộp (cắt theo mép khung).

    `exclude`: các vùng chữ KHÁC đang hiện cùng lúc (câu kề bên, dòng tiêu đề
    nằm sát) — điểm trong đó không tính, vì chữ đó cũng sẽ bị che.
    """
    w, h = img.size
    x0, y0 = box["x"], box["y"]
    x1, y1 = x0 + box["w"], y0 + box["h"]
    strips = [
        (x0 - ring, y0 - ring, x1 + ring, y0),  # trên
        (x0 - ring, y1, x1 + ring, y1 + ring),  # dưới
        (x0 - ring, y0, x0, y1),  # trái
        (x1, y0, x1 + ring, y1),  # phải
    ]
    out: list[tuple[int, int, int]] = []
    for a, b, c, d in strips:
        a, b, c, d = max(0, a), max(0, b), min(w, c), min(h, d)
        if c <= a or d <= b:
            continue
        region = img.crop((a, b, c, d))
        masked = False
        for e in exclude or []:
            ea, eb = max(a, e["x"]), max(b, e["y"])
            ec, ed = min(c, e["x"] + e["w"]), min(d, e["y"] + e["h"])
            if ec > ea and ed > eb:
                region.paste(_SENTINEL, (ea - a, eb - b, ec - a, ed - b))
                masked = True
        data = list(region.getdata())
        if masked:
            data = [p for p in data if p != _SENTINEL]
        # Lấy mẫu thưa cho nhanh: viền dài vài nghìn điểm là thừa.
        step = max(1, len(data) // 400)
        out.extend(data[::step])
    return out


def _stats(pixels: list[tuple[int, int, int]]) -> tuple[tuple[int, int, int], float]:
    n = len(pixels)
    mean = tuple(round(sum(p[i] for p in pixels) / n) for i in range(3))
    lum = [0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in pixels]
    m = sum(lum) / n
    std = (sum((v - m) ** 2 for v in lum) / n) ** 0.5
    return mean, std


def classify(images: list, box: dict, excludes: list[list[dict]] | None = None) -> dict:
    """{"mode": "flat", "color": "0xrrggbb"} hoặc {"mode": "plate"}.

    Đòi MỌI khung đều phẳng: chỉ cần một khung có tay/vật thò vào dải viền mà
    vẫn tô màu nền thì thành một hình chữ nhật đen cắt ngang vật đó. Khung lẫn
    chữ của vùng che khác (câu kề bên, dòng tiêu đề sát bên) được loại qua
    `excludes` — mỗi khung một danh sách.
    """
    means = []
    for i, img in enumerate(images):
        pixels = ring_pixels(img, box, exclude=excludes[i] if excludes else None)
        if len(pixels) < 20:
            return {"mode": PLATE}
        mean, std = _stats(pixels)
        if std > FLAT_LUMA_STD:
            return {"mode": PLATE}
        means.append(mean)
    if not means:
        return {"mode": PLATE}
    for i in range(3):
        if max(m[i] for m in means) - min(m[i] for m in means) > FLAT_FRAME_DRIFT:
            return {"mode": PLATE}
    color = tuple(sorted(m[i] for m in means)[len(means) // 2] for i in range(3))
    return {"mode": FLAT, "color": "0x%02x%02x%02x" % color}


def frames_between(frames: list[Path], start_ms: int, end_ms: int,
                   step_ms: int = 500) -> list[tuple[int, Path]]:
    """Tối đa SAMPLES (mốc ms, khung) rải đều trong [start, end].

    Khung i ứng với i*step_ms. Luôn gồm khung đầu và khung cuối của khoảng: tay
    hay thò vào đúng lúc chuyển câu.
    """
    if not frames:
        return []
    lo = max(0, min(len(frames) - 1, start_ms // step_ms))
    hi = max(lo, min(len(frames) - 1, end_ms // step_ms))
    count = min(SAMPLES, hi - lo + 1)
    idx = [lo] if count <= 1 else [lo + round(i * (hi - lo) / (count - 1)) for i in range(count)]
    return [(i * step_ms, frames[i]) for i in idx]


def _active(region: dict, t_ms: int, step_ms: int) -> bool:
    if "start_ms" not in region:
        return True
    return region["start_ms"] - step_ms <= t_ms <= region["end_ms"] + step_ms


def classify_region(frames: list[Path], region: dict, step_ms: int = 500,
                    others: list[dict] | None = None) -> dict:
    """Cách che cho một vùng (có `start_ms`/`end_ms` thì chỉ xét khoảng đó).

    `others`: mọi vùng che khác — vùng nào đang hiện ở khung được xét thì chữ
    của nó không tính là "vật thò vào".
    """
    if not frames:
        return {"mode": PLATE}
    if "start_ms" in region and "end_ms" in region:
        picked = frames_between(frames, region["start_ms"], region["end_ms"], step_ms)
    else:
        picked = frames_between(frames, 0, len(frames) * step_ms, step_ms)
    try:
        images = [_load(str(p)) for _, p in picked]
    except OSError:
        return {"mode": PLATE}
    excludes = [
        [o for o in others or [] if o is not region and _active(o, t, step_ms)]
        for t, _ in picked
    ]
    return classify(images, region, excludes)
