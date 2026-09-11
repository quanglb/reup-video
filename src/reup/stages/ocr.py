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

# Hai câu OCR liền nhau giống nhau tới mức này thì coi là một câu kéo dài.
SAME_TEXT_RATIO = 0.8


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


def group_lines(per_frame: list[str], step_ms: int) -> list[dict]:
    """Gộp khung liên tiếp cùng nội dung thành câu có start_ms/end_ms."""
    lines: list[dict] = []
    for index, text in enumerate(per_frame):
        start = index * step_ms
        if not text:
            continue
        if lines and _similar(lines[-1]["text"], text) and lines[-1]["end_ms"] == start:
            lines[-1]["end_ms"] = start + step_ms
            # Giữ bản dài hơn: khung giữa câu thường đọc được đủ chữ hơn khung đầu.
            if len(text) > len(lines[-1]["text"]):
                lines[-1]["text"] = text
        else:
            lines.append({"text": text, "start_ms": start, "end_ms": start + step_ms})
    return lines


def run(job: Job, cfg: Config) -> None:
    rect = json.loads(job.subrect_json.read_text(encoding="utf-8"))
    subtitle_regions = [r for r in rect["regions"] if r["kind"] == "subtitle"]

    info = probe(job.source_video)
    paths = extract_frames(job.source_video, job.root / "frames", fps=SAMPLE_FPS)
    step_ms = round(1000 / SAMPLE_FPS)

    per_frame = []
    for p in paths:
        boxes = recognize(p, info.width, info.height, OCR_LANGS)
        keep = [b for b in boxes if any(_inside(b, r) for r in subtitle_regions)]
        per_frame.append(" ".join(b.text for b in keep).strip())

    atomic_write(
        job.ocr_json,
        json.dumps(
            {"lines": group_lines(per_frame, step_ms)}, ensure_ascii=False, indent=2
        ),
    )


SPEC = StageSpec(name="ocr", produces=("ocr.json",), run=run)
