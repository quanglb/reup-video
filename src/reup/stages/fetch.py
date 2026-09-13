"""Stage 2 — tải video nguồn về thư mục job."""
from __future__ import annotations

from reup.adapters import douyin_export
from reup.adapters.manual import ManualSource
from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.ffmpeg import probe


def _download(job: Job, cfg: Config) -> None:
    """Job chọn từ file xuất Douyin có link CDN tải thẳng — nhanh hơn và không
    cần cookie. Link đó hết hạn sau vài giờ, nên hỏng thì vẫn thử yt-dlp, và báo
    cả hai lời khi cả hai cùng hỏng."""
    direct_error = ""
    try:
        if douyin_export.fetch_direct(job.root, job.source_video, job.source_info):
            return
    except (OSError, ValueError, KeyError) as exc:
        direct_error = f"tải thẳng từ CDN Douyin hỏng: {exc}"

    try:
        ManualSource(
            prefer_h264=cfg.profile.prefer_h264,
            js_runtime=cfg.fetch.js_runtime,
            remote_components=cfg.fetch.remote_components,
        ).fetch(job.source_url, job.source_video)
    except RuntimeError as exc:
        if not direct_error:
            raise
        raise RuntimeError(
            f"{direct_error}\n(link hết hạn thì xuất lại file ở tab Douyin)\n\n"
            f"yt-dlp cũng hỏng:\n{exc}"
        ) from exc


def run(job: Job, cfg: Config) -> None:
    _download(job, cfg)

    info = probe(job.source_video)
    if not info.has_audio:
        raise ValueError(
            f"video {job.source_url} không có audio — pipeline này cần tiếng nói để dịch"
        )
    if not info.has_video:
        raise ValueError(f"file tải về từ {job.source_url} không có stream video")


SPEC = StageSpec(name="fetch", produces=("source.mp4", "source.info.json"), run=run)
