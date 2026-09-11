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
    )
