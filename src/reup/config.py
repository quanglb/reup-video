"""Đọc config.toml thành các dataclass bất biến."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

AUDIO_MODES = ("separate", "drop_original")


def parse_bitrate(text: str) -> int:
    """Đổi cách viết của ffmpeg ("8M", "2500k", "800000") thành bit/giây."""
    s = str(text).strip()
    if not s:
        raise ValueError("bitrate rỗng")
    factor = 1
    if s[-1] in "kK":
        factor, s = 1_000, s[:-1]
    elif s[-1] in "mM":
        factor, s = 1_000_000, s[:-1]
    try:
        value = float(s)
    except ValueError:
        raise ValueError(f"bitrate {text!r} không đọc được. Ví dụ hợp lệ: 8M, 2500k, 800000")
    if value <= 0:
        raise ValueError(f"bitrate {text!r} phải dương")
    return round(value * factor)


@dataclass(frozen=True)
class ProfileConfig:
    concurrency: int
    whisper_model: str
    demucs_segment: int
    encoder: str
    video_bitrate: str = "8M"  # trần, không phải mức cố định — xem compose.pick_bitrate
    prefer_h264: bool = False  # chỉ bật trên máy không có hardware AV1 decode


@dataclass(frozen=True)
class AudioConfig:
    mode: str
    bgm_gain: float


@dataclass(frozen=True)
class TransformConfig:
    hflip: bool
    zoom: float
    speed: float


@dataclass(frozen=True)
class SubtitleConfig:
    font: str
    size: int
    outline: int
    position: str


@dataclass(frozen=True)
class ReviewConfig:
    auto_approve_b: bool


@dataclass(frozen=True)
class Config:
    profile_name: str
    profile: ProfileConfig
    audio: AudioConfig
    transform: TransformConfig
    subtitle: SubtitleConfig
    review: ReviewConfig


def load_config(path: Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))

    name = raw["profile"]["active"]
    profiles = {k: v for k, v in raw["profile"].items() if k != "active"}
    if name not in profiles:
        raise ValueError(
            f"profile.active = {name!r} nhưng không có mục [profile.{name}]. "
            f"Có sẵn: {sorted(profiles)}"
        )

    profile = ProfileConfig(**profiles[name])
    parse_bitrate(profile.video_bitrate)  # sai cú pháp thì gãy ngay lúc đọc config

    audio = raw["audio"]
    if audio["mode"] not in AUDIO_MODES:
        raise ValueError(
            f"audio.mode = {audio['mode']!r} không hợp lệ. Chọn một trong {AUDIO_MODES}"
        )

    return Config(
        profile_name=name,
        profile=profile,
        audio=AudioConfig(**audio),
        transform=TransformConfig(**raw["transform"]),
        subtitle=SubtitleConfig(**raw["subtitle"]),
        review=ReviewConfig(**raw["review"]),
    )
