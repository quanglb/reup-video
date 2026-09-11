"""Stage 2 — tải video nguồn về thư mục job."""
from __future__ import annotations

from reup.adapters.manual import ManualSource
from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.ffmpeg import probe


def run(job: Job, cfg: Config) -> None:
    ManualSource(prefer_h264=cfg.profile.prefer_h264).fetch(
        job.source_url, job.source_video
    )

    info = probe(job.source_video)
    if not info.has_audio:
        raise ValueError(
            f"video {job.source_url} không có audio — pipeline này cần tiếng nói để dịch"
        )
    if not info.has_video:
        raise ValueError(f"file tải về từ {job.source_url} không có stream video")


SPEC = StageSpec(name="fetch", produces=("source.mp4", "source.info.json"), run=run)
