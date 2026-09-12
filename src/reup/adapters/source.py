"""Biên giới ra các nền tảng video."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Candidate:
    platform: str
    video_id: str
    url: str
    title: str
    duration_ms: int
    view_count: int
    published_at: str
    # Link nhúng để xem thử ngay trong Web UI. Rỗng khi nền tảng không có
    # player nhúng công khai — UI khi đó chỉ mở link ra tab mới.
    embed_url: str = ""
    thumbnail: str = ""
    uploader: str = ""


@dataclass(frozen=True)
class FetchResult:
    video_path: Path
    info_path: Path
    duration_ms: int
    width: int
    height: int


class SourceAdapter(Protocol):
    name: str

    def list_trending(self, region: str, limit: int) -> list[Candidate]: ...

    def fetch(self, url: str, dest: Path) -> FetchResult: ...
