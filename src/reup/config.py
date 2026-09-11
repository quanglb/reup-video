"""Đọc config.toml thành các dataclass bất biến."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

AUDIO_MODES = ("separate", "drop_original")


@dataclass(frozen=True)
class ProfileConfig:
    concurrency: int
    whisper_model: str
    demucs_segment: int
    encoder: str


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

    audio = raw["audio"]
    if audio["mode"] not in AUDIO_MODES:
        raise ValueError(
            f"audio.mode = {audio['mode']!r} không hợp lệ. Chọn một trong {AUDIO_MODES}"
        )

    return Config(
        profile_name=name,
        profile=ProfileConfig(**profiles[name]),
        audio=AudioConfig(**audio),
        transform=TransformConfig(**raw["transform"]),
        subtitle=SubtitleConfig(**raw["subtitle"]),
        review=ReviewConfig(**raw["review"]),
    )
