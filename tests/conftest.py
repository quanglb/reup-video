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


@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    """Test không bao giờ nhắn bot thật.

    config.toml bật [notify], còn `reup` nạp .env thật lúc chạy lệnh. Đặt sẵn
    biến rỗng thì `load_dotenv` không ghi đè, nên token thật không lọt vào.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")


@pytest.fixture(autouse=True)
def no_web_auth(monkeypatch):
    """Test không bao giờ dùng mật khẩu web thật.

    Nếu REUP_WEB_PASSWORD được đặt trong shell chạy pytest, nó sẽ lọt vào
    create_app() và bật auth bất đắc dĩ. Xoá biến này trong test session
    để mọi test chạy với auth tắt (chế độ mặc định).
    """
    monkeypatch.delenv("REUP_WEB_PASSWORD", raising=False)
    monkeypatch.delenv("REUP_WEB_SECRET", raising=False)


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


from reup.config import (
    AudioConfig, Config, ProfileConfig, ReviewConfig, SubtitleConfig, TransformConfig,
)


@pytest.fixture
def cfg_fixture(tmp_path: Path) -> Config:
    """output_dir trỏ vào tmp_path chứ không phải "output" tương đối.

    Mặc định của ReviewConfig tính theo thư mục hiện hành, nên test nào chạy
    tới stage `export` sẽ ghi thẳng vào thư mục sản phẩm thật của dự án —
    `output/j1.mp4` nằm lẫn với video đã làm xong.
    """
    return Config(
        profile_name="test",
        profile=ProfileConfig(1, "tiny", 7, "h264_videotoolbox"),
        audio=AudioConfig("separate", 0.35),
        transform=TransformConfig(False, 1.0, 1.0),
        subtitle=SubtitleConfig("Be Vietnam Pro", 64, 4, "bottom"),
        review=ReviewConfig(False, output_dir=str(tmp_path / "output")),
    )


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Bản sao config.toml của dự án, dùng cho test CLI."""
    src = Path(__file__).resolve().parents[1] / "config.toml"
    dst = tmp_path / "config.toml"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst
