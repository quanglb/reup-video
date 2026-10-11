"""Stage 6 — dò vùng phụ đề gốc và watermark để biết chỗ nào cần blur.

Không phụ thuộc nhánh audio (stage 3-5), có thể chạy song song với nó.
"""
from __future__ import annotations

import json

from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.media.ffmpeg import probe
from reup.media.frames import extract_frames
from reup.media.ocr import recognize
from reup.subdetect import detect_regions, present_ranges

SAMPLE_FPS = 2.0
# Ngôn ngữ OCR: để cả Trung lẫn Anh vì nguồn là một trong hai.
OCR_LANGS = ["zh-Hans", "zh-Hant", "en-US"]


def scan(job: Job, fps: float = SAMPLE_FPS):
    """Trả (danh sách hộp chữ theo khung, width, height, bước thời gian ms)."""
    info = probe(job.source_video)
    paths = extract_frames(job.source_video, job.root / "frames", fps=fps)
    frames = [recognize(p, info.width, info.height, OCR_LANGS) for p in paths]
    return frames, info.width, info.height, round(1000 / fps)


def run(job: Job, cfg: Config) -> None:
    from reup.labels import find_labels, scan_frames

    frames, width, height, step_ms = scan(job)
    paths = sorted((job.root / "frames").glob("f_*.png"))
    regions = detect_regions(frames, width, height)
    covered = [{"x": r.x, "y": r.y, "w": r.w, "h": r.h} for r in regions]
    atomic_write(
        job.subrect_json,
        json.dumps(
            {
                "video_w": width,
                "video_h": height,
                "sample_fps": SAMPLE_FPS,
                "regions": [
                    {"x": r.x, "y": r.y, "w": r.w, "h": r.h,
                     "kind": r.kind, "coverage": r.coverage,
                     **({"text": r.text} if r.text else {})}
                    for r in regions
                ],
                "present_ranges": present_ranges(frames, width, height, step_ms),
                # Nhãn chữ Hán trong cảnh (vd. "黑桃10" cạnh lá bài): meta dịch,
                # compose che rồi đặt lại — không để chữ gốc sót trên thành phẩm.
                "labels": find_labels(
                    scan_frames(paths, width, height, frames, OCR_LANGS),
                    width, height, step_ms, covered,
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


SPEC = StageSpec(name="subdetect", produces=("subrect.json",), run=run)
