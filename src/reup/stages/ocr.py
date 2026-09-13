"""Stage 7 — đọc chữ trong vùng phụ đề, gom thành các câu kèm mốc thời gian.

Chỉ đọc trong vùng `subdetect` đã khoanh, nên bảng điểm và watermark không lọt
vào. Các khung liên tiếp có cùng nội dung được gộp thành một câu.
"""
from __future__ import annotations

import json

from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.media.ffmpeg import probe
from reup.media.frames import extract_frames
from reup.media.ocr import TextBox, recognize
from reup.stages.subdetect import OCR_LANGS, SAMPLE_FPS
from reup.textcolor import colors_from_frame

# Hai câu OCR liền nhau giống nhau tới mức này thì coi là một câu kéo dài.
SAME_TEXT_RATIO = 0.8
# Trong một khung, hộp thấp hơn ngần này so với hộp cao nhất là chữ phụ lọt vào
# vùng phụ đề (biển hiệu, logo) chứ không phải dòng phụ đề. Trên video thật,
# chữ biển cao 20-40px nằm ngay dưới phụ đề cao 100-130px và làm khung che phình
# gấp bốn nếu không lọc.
MIN_LINE_HEIGHT_RATIO = 0.55


def _similar(a: str, b: str) -> bool:
    if not a or not b:
        return a == b
    if a == b:
        return True
    import difflib

    return difflib.SequenceMatcher(None, a, b).ratio() >= SAME_TEXT_RATIO


def _inside(box: TextBox, region: dict) -> bool:
    """Tâm hộp nằm trong vùng phụ đề."""
    return (
        region["x"] <= box.cx <= region["x"] + region["w"]
        and region["y"] <= box.cy <= region["y"] + region["h"]
    )


def _union(a: dict | None, b: dict | None) -> dict | None:
    """Hợp hai hộp. `line_h` (chiều cao một dòng chữ) lấy lớn nhất của hai bên."""
    if a is None or b is None:
        return a or b
    right = max(a["x"] + a["w"], b["x"] + b["w"])
    bottom = max(a["y"] + a["h"], b["y"] + b["h"])
    x, y = min(a["x"], b["x"]), min(a["y"], b["y"])
    out = {"x": x, "y": y, "w": right - x, "h": bottom - y}
    line_h = max(a.get("line_h") or 0, b.get("line_h") or 0)
    if line_h:
        out["line_h"] = line_h
    # Màu chữ: giữ màu đầu tiên đoán được — cả câu thường cùng một màu.
    for key in ("color", "outline"):
        if a.get(key) or b.get(key):
            out[key] = a.get(key) or b.get(key)
    return out


def main_text_boxes(boxes: list[TextBox]) -> list[TextBox]:
    """Bỏ chữ phụ thấp hơn hẳn dòng phụ đề cao nhất trong cùng khung."""
    if not boxes:
        return []
    tallest = max(b.h for b in boxes)
    return [b for b in boxes if b.h >= tallest * MIN_LINE_HEIGHT_RATIO]


def group_lines(
    per_frame: list[str], step_ms: int, boxes: list[dict | None] | None = None
) -> list[dict]:
    """Gộp khung liên tiếp cùng nội dung thành câu có start_ms/end_ms.

    `boxes` là hộp bao chữ của từng khung. Có thì mỗi câu mang theo `box` là hợp
    các hộp của nó — compose che đúng chỗ đó, đúng lúc câu đó hiện.
    """
    lines: list[dict] = []
    for index, text in enumerate(per_frame):
        start = index * step_ms
        if not text:
            continue
        box = boxes[index] if boxes else None
        if lines and _similar(lines[-1]["text"], text) and lines[-1]["end_ms"] == start:
            lines[-1]["end_ms"] = start + step_ms
            # Giữ bản dài hơn: khung giữa câu thường đọc được đủ chữ hơn khung đầu.
            if len(text) > len(lines[-1]["text"]):
                lines[-1]["text"] = text
            if boxes:
                lines[-1]["box"] = _union(lines[-1].get("box"), box)
        else:
            line = {"text": text, "start_ms": start, "end_ms": start + step_ms}
            if box:
                line["box"] = box
            lines.append(line)
    return lines


def run(job: Job, cfg: Config) -> None:
    rect = json.loads(job.subrect_json.read_text(encoding="utf-8"))
    subtitle_regions = [r for r in rect["regions"] if r["kind"] == "subtitle"]

    info = probe(job.source_video)
    paths = extract_frames(job.source_video, job.root / "frames", fps=SAMPLE_FPS)
    step_ms = round(1000 / SAMPLE_FPS)

    per_frame = []
    frame_boxes: list[dict | None] = []
    for p in paths:
        boxes = recognize(p, info.width, info.height, OCR_LANGS)
        keep = main_text_boxes(
            [b for b in boxes if any(_inside(b, r) for r in subtitle_regions)]
        )
        per_frame.append(" ".join(b.text for b in keep).strip())
        union = None
        for b in keep:
            union = _union(union, {"x": b.x, "y": b.y, "w": b.w, "h": b.h, "line_h": b.h})
        if keep:
            # Đoán màu trên hộp cao nhất: dòng phụ đề chính, chữ to nhất, ít nhiễu.
            main = max(keep, key=lambda b: b.h)
            union.update(colors_from_frame(p, {"x": main.x, "y": main.y, "w": main.w, "h": main.h}))
        frame_boxes.append(union)

    atomic_write(
        job.ocr_json,
        json.dumps(
            {
                "video_w": info.width,
                "video_h": info.height,
                "step_ms": step_ms,
                "lines": group_lines(per_frame, step_ms, frame_boxes),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


SPEC = StageSpec(name="ocr", produces=("ocr.json",), run=run)
