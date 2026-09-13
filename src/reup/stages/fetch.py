"""Stage 2 — tải video nguồn về thư mục job."""
from __future__ import annotations

import json
from pathlib import Path

from reup.adapters import douyin_export
from reup.adapters.manual import ManualSource
from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.ffmpeg import probe


def exports_dir(job: Job) -> Path:
    """Cạnh thư mục jobs, giống `reup web` lưu file xuất Douyin."""
    return job.root.parent.parent / "douyin-exports"


def fresher_link(job: Job) -> str | None:
    """Link tải MỚI HƠN cho đúng video này, lấy từ các file xuất gần nhất.

    Link CDN Douyin có chữ ký và chết sau vài giờ. Người dùng hay xuất lại cùng
    kênh trong ngày — file mới hơn đó có link còn sống cho chính video này, khỏi
    bắt chọn lại từ đầu.
    """
    meta_path = job.root / douyin_export.DIRECT_FILE
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    video_id, old = meta.get("video_id"), meta.get("media_url")
    folder = exports_dir(job)
    if not (video_id and folder.is_dir()):
        return None
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            found = douyin_export.load_export(path)
        except (OSError, ValueError):
            continue
        for c in found:
            if c.video_id == video_id and c.media_url and c.media_url != old:
                meta["media_url"] = c.media_url
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                return c.media_url
    return None


def _platform_cookies(cfg: Config, url: str) -> dict:
    for name in ("douyin", "tiktok"):
        if name in url:
            opts = cfg.discover.for_platform(name)
            return {"cookies_from_browser": opts.cookies_from_browser, "cookie_file": opts.cookie_file}
    return {}


def _download(job: Job, cfg: Config) -> None:
    """Job chọn từ file xuất Douyin có link CDN tải thẳng — nhanh hơn và không
    cần cookie. Link đó hết hạn sau vài giờ, nên hỏng thì:
    1. tìm link mới hơn của đúng video trong các file xuất gần nhất và thử lại,
    2. rồi mới tới yt-dlp (kèm cookie trong [discover.<nền tảng>]),
    và báo đủ các lời khi tất cả cùng hỏng, kèm cách sửa."""
    direct_error = ""
    try:
        if douyin_export.fetch_direct(job.root, job.source_video, job.source_info):
            return
    except (OSError, ValueError, KeyError) as exc:
        direct_error = f"tải thẳng từ CDN Douyin hỏng: {exc}"
        if fresher_link(job):
            try:
                douyin_export.fetch_direct(job.root, job.source_video, job.source_info)
                return
            except (OSError, ValueError, KeyError) as again:
                direct_error += f"\nlink mới hơn trong file xuất cũng hỏng: {again}"

    cookies = _platform_cookies(cfg, job.source_url)
    try:
        ManualSource(
            prefer_h264=cfg.profile.prefer_h264,
            js_runtime=cfg.fetch.js_runtime,
            remote_components=cfg.fetch.remote_components,
            **cookies,
        ).fetch(job.source_url, job.source_video)
    except RuntimeError as exc:
        if not direct_error:
            raise
        hint = (
            "CÁCH SỬA (chọn một):\n"
            "  1. Tab Douyin: chạy lại script xuất file kênh, nạp file mới, rồi bấm Chạy lại\n"
            "     job này — bước tải tự lấy link mới cho đúng video.\n"
            "  2. Đặt cookies_from_browser = \"chrome\" trong [discover.douyin] của\n"
            "     config.toml (Chrome đã mở douyin.com) để yt-dlp tải bằng cookie."
        )
        if cookies.get("cookies_from_browser") or cookies.get("cookie_file"):
            hint = (
                "Đã dùng cookie trong config mà vẫn hỏng: mở douyin.com trên trình duyệt đó\n"
                "cho cookie mới, hoặc xuất lại file kênh ở tab Douyin rồi Chạy lại."
            )
        raise RuntimeError(
            f"{direct_error}\n(link tải trong file xuất đã hết hạn)\n\n"
            f"yt-dlp cũng hỏng:\n{exc}\n\n{hint}"
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
