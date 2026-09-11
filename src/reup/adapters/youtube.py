"""Quét video ngắn đang thịnh hành trên YouTube bằng yt-dlp.

`/feed/trending` đã chết — yt-dlp báo "channel/playlist does not exist and the
URL redirected to youtube.com home page". Đường còn sống là trang hashtag, trả
về đúng video dạng Shorts kèm id, tiêu đề và độ dài.

Đây là adapter trending duy nhất được hiện thực. TikTok và Douyin cần cookie và
IP Trung Quốc (spec R1) nên để ngoài; adapter `manual` luôn sống, nên không có
crawler nào thì pipeline vẫn chạy được bằng cách dán link.
"""
from __future__ import annotations

import json
import subprocess

from reup.adapters.source import Candidate, FetchResult
from reup.adapters.manual import ManualSource

PLATFORM = "youtube"
# Spec §3: đích là video dưới ~3 phút, 9:16.
MAX_DURATION_S = 180
MIN_DURATION_S = 5
DEFAULT_HASHTAG = "shorts"


class DiscoverError(RuntimeError):
    pass


def feed_url(region: str, hashtag: str = DEFAULT_HASHTAG) -> str:
    """`region` chưa dùng được: trang hashtag không nhận tham số vùng.

    Giữ tham số cho đúng interface và để adapter khác dùng.
    """
    return f"https://www.youtube.com/hashtag/{hashtag}"


def parse_entry(raw: dict) -> Candidate | None:
    video_id = raw.get("id")
    if not video_id:
        return None
    duration = raw.get("duration")
    duration_s = float(duration) if duration else 0.0
    if duration_s and not (MIN_DURATION_S <= duration_s <= MAX_DURATION_S):
        return None
    return Candidate(
        platform=PLATFORM,
        video_id=video_id,
        url=f"https://www.youtube.com/shorts/{video_id}",
        title=(raw.get("title") or "").strip(),
        duration_ms=round(duration_s * 1000),
        view_count=int(raw.get("view_count") or 0),
        published_at=str(raw.get("upload_date") or ""),
    )


class YouTubeSource:
    name = PLATFORM

    def __init__(self, hashtag: str = DEFAULT_HASHTAG) -> None:
        self.hashtag = hashtag

    def list_trending(self, region: str, limit: int) -> list[Candidate]:
        if limit <= 0:
            raise ValueError(f"limit phải dương, nhận {limit}")
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-json",
            "--playlist-end", str(limit * 2),  # lấy dư vì sẽ lọc theo độ dài
            feed_url(region, self.hashtag),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 and not proc.stdout.strip():
            raise DiscoverError(
                f"yt-dlp không quét được trending (mã {proc.returncode}).\n"
                "YouTube hay đổi cấu trúc trang; dán link thủ công bằng "
                "`reup add <url>` trong lúc chờ sửa.\n"
                f"stderr:\n{proc.stderr[-800:]}"
            )

        out: list[Candidate] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                candidate = parse_entry(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
            if candidate is not None:
                out.append(candidate)
            if len(out) >= limit:
                break
        return out

    def fetch(self, url, dest) -> FetchResult:
        # Tải về vẫn là việc của yt-dlp, giống hệt nguồn thủ công.
        return ManualSource().fetch(url, dest)
