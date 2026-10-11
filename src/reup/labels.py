"""Nhãn chữ Hán trong cảnh — chú thích kiểu "黑桃10" đặt cạnh lá bài, đổi chỗ theo cảnh.

Không phải phụ đề (không nằm ở dải dưới, không đổi theo lời) cũng không phải
chữ cố định cả video, nên subdetect xếp vào "scene" và trước đây bị bỏ qua — để
nguyên chữ Hán trên thành phẩm, đúng lỗi "chữ nước ngoài còn sót" mà stage
policy chặn (job 97290706). Ở đây gom chúng thành các "vệt" theo thời gian để
dịch rồi đặt lại như phụ đề.

Hàm thuần: nhận hộp chữ theo từng khung, không đụng Vision hay ffmpeg.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter

from reup.media.ocr import TextBox

_HAN = re.compile(r"[一-鿿]")
MIN_CONF = 0.4
# Hai khung liền nhau cùng một nhãn: chữ giống ít nhất ngần này, tâm lệch không
# quá ngần này so với chiều cao khung (nhãn đi theo vật thì nhảy chỗ — tách vệt).
SAME_TEXT = 0.6
SAME_PLACE = 0.06
MIN_FRAMES = 2  # >= 1 giây ở 2 khung/giây; nhấp nháy một khung là OCR nhiễu
PAD = 8


def _inside(box: TextBox, region: dict) -> bool:
    return (region["x"] <= box.cx <= region["x"] + region["w"]
            and region["y"] <= box.cy <= region["y"] + region["h"])


def scan_frames(paths: list, width: int, height: int, horizontal: list[list[TextBox]],
                langs: list[str]) -> list[list[TextBox]]:
    """Hộp chữ ngang đã có + chữ dọc đọc thêm bằng LiveText, theo từng khung."""
    from reup.media.ocr import recognize_vertical

    return [h + recognize_vertical(p, width, height, langs) for p, h in zip(paths, horizontal)]


def find_labels(frames: list[list[TextBox]], width: int, height: int, step_ms: int,
                covered: list[dict]) -> list[dict]:
    """Các vệt nhãn: {x, y, w, h, start_ms, end_ms, text}. `covered`: vùng đã che."""
    tracks: list[dict] = []
    open_tracks: list[dict] = []
    for index, boxes in enumerate(frames):
        cands = [
            b for b in boxes
            if b.confidence >= MIN_CONF and _HAN.search(b.text)
            and not any(_inside(b, r) for r in covered)
        ]
        still_open = []
        for b in cands:
            match = None
            for t in open_tracks:
                last = t["boxes"][-1]
                if (t["last"] == index - 1
                        and abs(last.cx - b.cx) <= height * SAME_PLACE
                        and abs(last.cy - b.cy) <= height * SAME_PLACE
                        and difflib.SequenceMatcher(None, last.text, b.text).ratio() >= SAME_TEXT):
                    match = t
                    break
            if match is None:
                match = {"boxes": [], "first": index, "last": index}
                tracks.append(match)
            match["boxes"].append(b)
            match["last"] = index
            still_open.append(match)
        open_tracks = still_open

    out = []
    for t in tracks:
        if len(t["boxes"]) < MIN_FRAMES:
            continue
        bs = t["boxes"]
        x0 = max(0, min(b.x for b in bs) - PAD)
        y0 = max(0, min(b.y for b in bs) - PAD)
        x1 = min(width, max(b.x + b.w for b in bs) + PAD)
        y1 = min(height, max(b.y + b.h for b in bs) + PAD)
        text = Counter(b.text for b in bs).most_common(1)[0][0]
        half = step_ms // 2
        out.append({
            "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
            "start_ms": max(0, t["first"] * step_ms - half),
            "end_ms": (t["last"] + 1) * step_ms + half,
            "text": text,
            # Cỡ một chữ: chiều cao dòng ngang, hoặc bề ngang cột chữ dọc.
            "line_h": max(min(b.w, b.h) for b in bs),
            "vertical": all(b.h > b.w * 1.5 for b in bs),
        })
    return out
