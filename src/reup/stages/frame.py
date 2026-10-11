"""Stage dựng của dự án có ảnh khung (vd. LOL): đặt video gốc vào vùng xanh.

Luồng: tải về → precheck → tiêu đề (meta) → đặt khung + in tiêu đề → policy →
xuất. Không dịch, không lồng tiếng, không che phụ đề gốc.
"""
from __future__ import annotations

import os

from reup import projects
from reup.config import Config, parse_bitrate
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.ffmpeg import probe, run_ffmpeg

# Khung ra dọc 9:16.
FRAME_W, FRAME_H = 1080, 1920
# Khung 1080x1920 nét hơn hầu hết nguồn Douyin; 6M là đủ, trần config vẫn thắng.
FRAME_BPS = 6_000_000


# Màu nền lấp phần dư của vùng đặt video — tông tối của khung LOL. Bản cũ lấp
# bằng chính video phóng to làm mờ: hai dải mờ hai bên là dấu hiệu reup.
FILL_COLOR = "0x0b141d"
# Video lệch tỉ lệ với vùng đặt ít hơn ngần này thì cắt cho đầy vùng (mất tối
# đa ~15% mép) thay vì để dải nền.
MAX_COVER_CROP = 0.15


def cover_crop_loss(src_w: int, src_h: int, w: int, h: int) -> float:
    """Phần hình bị cắt mất nếu phóng video cho phủ kín vùng w x h."""
    if not (src_w and src_h and w and h):
        return 1.0
    src, box = src_w / src_h, w / h
    return 1 - min(src, box) / max(src, box)


def build_frame_chain(
    rect: tuple[int, int, int, int], frame_input: int, src_w: int = 0, src_h: int = 0
) -> str:
    """Video đặt vào vùng xanh: lệch tỉ lệ ít thì cắt cho kín vùng, lệch nhiều
    thì giữ trọn hình và lấp phần dư bằng màu nền tối (không blur).

    Ảnh khung (vùng xanh đã khoét trong suốt) dán LÊN TRÊN video: video là đầu
    vào chính nên quyết định fps và độ dài thành phẩm.
    """
    x, y, w, h = rect
    if cover_crop_loss(src_w, src_h, w, h) <= MAX_COVER_CROP:
        fit = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1[ffg];"
        )
    else:
        fit = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"scale=trunc(iw/2)*2:trunc(ih/2)*2,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={FILL_COLOR},setsar=1[ffg];"
        )
    return (
        fit
        + f"[ffg]pad={FRAME_W}:{FRAME_H}:{x}:{y}:color=black,setsar=1[fp];"
        f"[fp][{frame_input}:v]overlay=0:0,format=yuv420p[v]"
    )


def draw_title(frame_png, rect: tuple[int, int, int, int], title: str, font) -> bool:
    """In tiêu đề tiếng Việt lên phần trên khung (phía trên vùng đặt video).

    Đây là phần "chỉnh sửa sáng tạo" tối thiểu của dự án khung: không có nó,
    thành phẩm chỉ là video gốc đặt vào ảnh nền — đúng định nghĩa nội dung
    không phải nguyên tác của TikTok.
    """
    from PIL import Image

    from reup.subtitle import render_fitted

    title = (title or "").strip()
    _, y, _, _ = rect
    if not title or y < FRAME_H * 0.12:
        return False
    box_w = int(FRAME_W * 0.88)
    box_h = int(y - FRAME_H * 0.10)
    tmp = frame_png.with_name("title.png")
    w, h, _ = render_fitted(
        title, tmp, font, box_w, box_h, 0.08, max_size=86,
        fill=(255, 255, 255), stroke=(0, 0, 0),
    )
    with Image.open(frame_png) as base, Image.open(tmp) as text:
        base = base.convert("RGBA")
        pos = ((FRAME_W - w) // 2, int(FRAME_H * 0.06 + (box_h - h) / 2))
        base.alpha_composite(text.convert("RGBA"), pos)
        base.save(frame_png)
    return True


def run(job: Job, cfg: Config) -> None:
    project = projects.get(job.settings.get("project"))
    if project is None:
        raise ValueError(f"job {job.id} không thuộc dự án có khung")
    job.render_dir.mkdir(parents=True, exist_ok=True)
    frame_png = job.render_dir / "frame.png"
    rect = projects.render_frame(project.frame, frame_png, FRAME_W, FRAME_H)
    if job.meta_json.exists():
        import json

        from reup.subtitle import find_font

        title = json.loads(job.meta_json.read_text(encoding="utf-8")).get("title", "")
        draw_title(frame_png, rect, title, find_font(cfg.subtitle.font))

    info = probe(job.source_video)
    timeout_s = max(60.0, info.duration_ms / 1000 * 4)
    bitrate = min(FRAME_BPS, parse_bitrate(cfg.profile.video_bitrate))
    audio = ["-map", "0:a", "-c:a", "aac", "-b:a", "192k"] if info.has_audio else []

    tmp_mp4 = job.final_mp4.with_suffix(".tmp.mp4")
    if tmp_mp4.exists():
        tmp_mp4.unlink()
    run_ffmpeg([
        "-i", str(job.source_video), "-i", str(frame_png),
        "-filter_complex", build_frame_chain(rect, 1, info.width, info.height),
        "-map", "[v]", *audio,
        "-c:v", cfg.profile.encoder, "-b:v", str(bitrate),
        "-movflags", "+faststart",
        str(tmp_mp4),
    ], timeout_s=timeout_s)
    os.replace(tmp_mp4, job.final_mp4)


SPEC = StageSpec(name="frame", produces=("render/final.mp4",), run=run)
