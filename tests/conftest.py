"""Fixture media sinh bằng ffmpeg lúc chạy test — không commit file nhị phân."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE_W, FIXTURE_H = 540, 960


@pytest.fixture(scope="session", autouse=True)
def require_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            pytest.skip(f"cần {tool} trong PATH", allow_module_level=True)


def _run(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True)


@pytest.fixture
def sample_video(tmp_path: Path) -> Path:
    """6 giây, 540x960, có cả video lẫn audio."""
    out = tmp_path / "sample.mp4"
    _run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size={FIXTURE_W}x{FIXTURE_H}:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6:sample_rate=48000",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out),
    ])
    return out


@pytest.fixture
def sample_wav(tmp_path: Path) -> Path:
    """2 giây, 48kHz stereo."""
    out = tmp_path / "sample.wav"
    _run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2:sample_rate=48000",
        "-ac", "2", "-c:a", "pcm_s16le", str(out),
    ])
    return out
