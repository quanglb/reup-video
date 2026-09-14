"""Bọc ffmpeg và ffprobe. Hàm thuần — không biết Job hay Config là gì."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration_ms: int
    width: int
    height: int
    has_video: bool
    has_audio: bool
    video_bps: int = 0  # 0 khi ffprobe không báo — người gọi phải tự lo


def run_ffmpeg(args: list[str]) -> str:
    """Chạy ffmpeg với -y và -loglevel error. Trả stderr, ném FFmpegError khi lỗi."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffmpeg thoát với mã {proc.returncode}\n"
            f"lệnh: {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc.stderr


def probe(path: Path) -> MediaInfo:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffprobe thoát với mã {proc.returncode} khi đọc {path}\n"
            f"stderr:\n{proc.stderr}"
        )
    raw = json.loads(proc.stdout)
    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(raw.get("format", {}).get("duration", 0.0))
    return MediaInfo(
        duration_ms=round(duration * 1000),
        width=int(video["width"]) if video else 0,
        height=int(video["height"]) if video else 0,
        has_video=video is not None,
        has_audio=audio is not None,
        video_bps=_video_bps(raw, video, duration),
    )


def _video_bps(raw: dict, video: dict | None, duration: float) -> int:
    """Bitrate của riêng stream video.

    mp4 từ yt-dlp thường không ghi `bit_rate` ở stream, nên phải suy ra từ
    `format.bit_rate` trừ đi phần audio, hoặc cuối cùng là từ kích thước file.
    """
    if video is None:
        return 0
    if video.get("bit_rate"):
        return int(float(video["bit_rate"]))

    fmt = raw.get("format", {})
    audio_bps = sum(
        int(float(s["bit_rate"]))
        for s in raw.get("streams", [])
        if s.get("codec_type") == "audio" and s.get("bit_rate")
    )
    if fmt.get("bit_rate"):
        return max(0, int(float(fmt["bit_rate"])) - audio_bps)
    if fmt.get("size") and duration > 0:
        total = int(float(fmt["size"])) * 8 / duration
        return max(0, round(total - audio_bps))
    return 0


def has_filter(name: str) -> bool:
    """ffmpeg có filter này không.

    Bản ffmpeg của Homebrew hiện không build libass nên thiếu `ass`,
    `subtitles` và `drawtext`. Hỏi trước còn hơn để filtergraph gãy giữa chừng
    với một thông báo khó hiểu.
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True
    )
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False
