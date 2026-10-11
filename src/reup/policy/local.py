"""Kiểm thành phẩm ngay trên máy — Apple Vision, ffmpeg, Pillow. Không tốn token.

Chạy TRƯỚC lượt gọi AI: lỗi cứng (mã QR, chữ gốc sót nhiều) chặn luôn mà khỏi
gọi AI; phần còn lại được tóm thành vài dòng đưa vào prompt để AI khỏi phải tự
dò lại.
"""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from reup.media.ffmpeg import probe, run_ffmpeg
from reup.policy import rules

# Rải khung trong khoảng này của thời lượng: khung đầu thường đen, khung cuối
# hay là outro — outro vẫn được xét riêng ở khung cuối cùng.
SPAN = (0.04, 0.985)
OCR_WIDTH = 720
OCR_LANGS = ["zh-Hans", "zh-Hant", "en-US"]
# Dòng chữ Hán coi là "chữ gốc sót": đủ tin cậy, đủ dài, đủ to để đọc được.
MIN_CJK_CHARS = 2
MIN_CJK_CONF = 0.45
MIN_TEXT_H_RATIO = 0.012
# Màn hình kết thúc / kêu gọi của kênh gốc.
OUTRO = re.compile(
    r"thanks? for watching|subscribe|đăng ký kênh|follow me|关注|点赞|订阅|下期见",
    re.IGNORECASE,
)


@dataclass
class LocalReport:
    frames: list[Path] = field(default_factory=list)
    times_s: list[float] = field(default_factory=list)
    cjk_frames: list[tuple[float, str]] = field(default_factory=list)
    outro: list[tuple[float, str]] = field(default_factory=list)
    qr: list[tuple[float, str]] = field(default_factory=list)
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    video_kbps: int = 0

    @property
    def cjk_ratio(self) -> float:
        return len(self.cjk_frames) / len(self.frames) if self.frames else 0.0

    def to_dict(self) -> dict:
        return {
            "frames": len(self.frames),
            "cjk_frames": [[round(t, 1), s] for t, s in self.cjk_frames],
            "cjk_ratio": round(self.cjk_ratio, 3),
            "outro": [[round(t, 1), s] for t, s in self.outro],
            "qr": [[round(t, 1), s] for t, s in self.qr],
            "width": self.width,
            "height": self.height,
            "duration_s": round(self.duration_s, 2),
            "video_kbps": self.video_kbps,
        }


def sample_times(duration_s: float, n: int) -> list[float]:
    if duration_s <= 0 or n <= 0:
        return []
    a, b = SPAN
    if n == 1:
        return [duration_s / 2]
    return [duration_s * (a + (b - a) * i / (n - 1)) for i in range(n)]


def extract(video: Path, times: list[float], out_dir: Path, width: int = OCR_WIDTH) -> list[Path]:
    """Một JPEG cho mỗi mốc thời gian (seek nhanh từng mốc, không giải mã cả video)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("p_*.jpg"):
        old.unlink()
    paths = []
    for i, t in enumerate(times):
        p = out_dir / f"p_{i:02d}.jpg"
        run_ffmpeg([
            "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
            "-vf", f"scale={width}:-2", "-q:v", "3", str(p),
        ])
        if p.exists():
            paths.append(p)
    return paths


def detect_qr(path: Path) -> list[str]:
    """Mã QR/barcode trong ảnh, qua Vision. Máy không có Vision thì trả rỗng."""
    try:
        import Vision
        from Foundation import NSURL
    except Exception:
        return []
    url = NSURL.fileURLWithPath_(str(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    req = Vision.VNDetectBarcodesRequest.alloc().init()
    ok, _ = handler.performRequests_error_([req], None)
    if not ok:
        return []
    found = []
    for r in req.results() or []:
        found.append(str(r.payloadStringValue() or r.symbology() or "?"))
    return found


def _ocr(path: Path, w: int, h: int):
    try:
        from reup.media.ocr import recognize
    except Exception:
        return []
    try:
        return recognize(path, w, h, OCR_LANGS)
    except Exception:
        return []


def _ocr_vertical(path: Path, w: int, h: int):
    """Chữ Hán viết dọc — Vision thường bỏ sót, LiveText đọc được."""
    try:
        from reup.media.ocr import recognize_vertical

        return recognize_vertical(path, w, h, OCR_LANGS)
    except Exception:
        return []


def scan(video: Path, work_dir: Path, n: int) -> LocalReport:
    info = probe(video)
    rep = LocalReport(
        width=info.width, height=info.height, duration_s=info.duration_ms / 1000,
        video_kbps=round(getattr(info, "video_bps", 0) / 1000),
    )
    rep.times_s = sample_times(rep.duration_s, n)
    rep.frames = extract(video, rep.times_s, work_dir)
    if not rep.frames:
        return rep
    from PIL import Image

    with Image.open(rep.frames[0]) as im:
        fw, fh = im.size
    for t, p in zip(rep.times_s, rep.frames):
        boxes = _ocr(p, fw, fh) + _ocr_vertical(p, fw, fh)
        cjk = [
            b.text for b in boxes
            if b.confidence >= MIN_CJK_CONF
            and b.h >= fh * MIN_TEXT_H_RATIO
            and len(re.findall(r"[一-鿿]", b.text)) >= MIN_CJK_CHARS
        ]
        if cjk:
            rep.cjk_frames.append((t, " ".join(cjk)[:60]))
        outro = [b.text for b in boxes if OUTRO.search(b.text)]
        if outro:
            rep.outro.append((t, " ".join(outro)[:60]))
        for code in detect_qr(p):
            rep.qr.append((t, code[:60]))
    return rep


def contact_sheet(frames: list[Path], k: int, width: int, quality: int = 70) -> bytes:
    """Ghép k khung rải đều thành MỘT ảnh JPEG — một ảnh thay vì k ảnh là ít token nhất."""
    from PIL import Image, ImageDraw

    if not frames:
        return b""
    k = max(1, min(k, len(frames)))
    picked = [frames[round(i * (len(frames) - 1) / max(1, k - 1))] for i in range(k)]
    cols = 3 if k > 2 else k
    rows = math.ceil(k / cols)
    cell_w = width // cols
    cells = []
    for p in picked:
        with Image.open(p) as im:
            im = im.convert("RGB")
            ratio = cell_w / im.width
            cells.append(im.resize((cell_w, max(1, round(im.height * ratio)))))
    cell_h = max(c.height for c in cells)
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    for i, c in enumerate(cells):
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        sheet.paste(c, (x, y))
        draw.text((x + 6, y + 4), str(i + 1), fill=(255, 255, 0))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def text_hits(meta: dict, subtitle_text: str) -> list[rules.Hit]:
    """Luật chữ trên mọi thứ người xem đọc/nghe được: tiêu đề, mô tả, hashtag,
    phụ đề/giọng đọc, chữ đặt đè lên hình."""
    hits = rules.scan_text(meta.get("title", ""), "tiêu đề")
    hits += rules.scan_text(meta.get("description", ""), "mô tả")
    for tag in meta.get("hashtags") or []:
        hits += rules.scan_hashtag(tag)
    hits += rules.scan_text(subtitle_text, "phụ đề")
    for h in meta.get("headers") or []:
        hits += rules.scan_text(h.get("vi", ""), "chữ trên hình")
    return hits
