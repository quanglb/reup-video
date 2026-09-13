"""Quét video Douyin bằng yt-dlp.

Douyin khó hơn TikTok một bậc: ngoài cookie còn cần IP ra được Trung Quốc
(spec R1). Đường quét là trang người dùng `https://www.douyin.com/user/<sec_uid>`
hoặc nguyên một URL người dùng dán vào; Douyin không mở trang hashtag cho
khách nên `query` hầu như luôn là user hoặc URL.

Thực tế yt-dlp gần như không quét được Douyin. Đường chạy được là `query` trỏ
tới file `.json` xuất từ console trình duyệt — xem `douyin_export.py`.

Player nhúng: `https://open.douyin.com/player/video?vid=<id>&autoplay=0`. Trang
này chỉ chạy khi `vid` là item id công khai — có video nhúng ra ô trắng, nên UI
luôn kèm link mở thẳng sang Douyin.
"""
from __future__ import annotations

from reup.adapters.crawl import (
    DiscoverError,
    cookie_args,
    dump_flat,
    duration_ok,
    pick_thumbnail,
)
from reup.adapters.manual import ManualSource
from reup.adapters.source import Candidate, FetchResult

PLATFORM = "douyin"
SEC_UID_PREFIX = "MS4wLjABAAAA"


def feed_url(query: str) -> str:
    q = (query or "").strip()
    if not q:
        raise ValueError(
            "Douyin không có trang trending mở: cần id người dùng hoặc URL"
        )
    if q.startswith("http://") or q.startswith("https://"):
        return q
    # sec_uid luôn bắt đầu bằng tiền tố base64 này. Từ khoá như "抖音" mà ghép
    # thành /user/抖音 thì yt-dlp chỉ báo "Unsupported URL", không nói lỗi ở đâu.
    if not q.startswith(SEC_UID_PREFIX):
        raise ValueError(
            f"Douyin không tìm theo từ khoá ({q!r}). Nạp file .json xuất từ trang "
            "kênh (hướng dẫn ở khung trên), hoặc nhập sec_uid "
            f"({SEC_UID_PREFIX}…) / URL kênh."
        )
    return f"https://www.douyin.com/user/{q}"


def embed_url(video_id: str) -> str:
    return f"https://open.douyin.com/player/video?vid={video_id}&autoplay=0"


def parse_entry(raw: dict) -> Candidate | None:
    video_id = str(raw.get("id") or "").strip()
    if not video_id:
        return None
    duration = raw.get("duration")
    duration_s = float(duration) if duration else 0.0
    if not duration_ok(duration_s):
        return None
    url = raw.get("url") or raw.get("webpage_url")
    if not url or not str(url).startswith("http"):
        url = f"https://www.douyin.com/video/{video_id}"
    return Candidate(
        platform=PLATFORM,
        video_id=video_id,
        url=str(url),
        title=(raw.get("title") or raw.get("description") or "").strip(),
        duration_ms=round(duration_s * 1000),
        view_count=int(raw.get("view_count") or 0),
        published_at=str(raw.get("upload_date") or ""),
        embed_url=embed_url(video_id),
        thumbnail=pick_thumbnail(raw),
        uploader=(raw.get("uploader") or raw.get("channel") or "").strip(),
    )


class DouyinSource:
    name = PLATFORM

    def __init__(
        self,
        query: str = "",
        cookies_from_browser: str | None = None,
        cookie_file: str | None = None,
    ) -> None:
        self.query = query
        self.cookies_from_browser = cookies_from_browser
        self.cookie_file = cookie_file

    @property
    def lists_all(self) -> bool:
        """File xuất đọc tại chỗ nên trả cả kênh; người gọi tự sắp và cắt."""
        from reup.adapters.douyin_export import is_export

        return is_export(self.query)

    def describe(self) -> tuple[str, str]:
        q = (self.query or "").strip()
        if self.lists_all:
            return q, "file xuất"
        return feed_url(self.query), "URL" if q.startswith("http") else "kênh"

    def list_trending(self, region: str, limit: int) -> list[Candidate]:
        if self.lists_all:
            from pathlib import Path

            from reup.adapters.douyin_export import load_export

            return load_export(Path(self.query.strip()))
        args = cookie_args(self.cookies_from_browser, self.cookie_file)
        try:
            entries = dump_flat(feed_url(self.query), limit, args)
        except DiscoverError as exc:
            raise DiscoverError(
                f"{exc}\n\nDouyin đòi cookie và IP ra được Trung Quốc (spec R1).\n"
                "Đặt [discover.douyin] cookies_from_browser hoặc cookie_file trong "
                "config.toml, hoặc dán link thủ công bằng `reup add <url>`."
            ) from exc

        out: list[Candidate] = []
        for raw in entries:
            candidate = parse_entry(raw)
            if candidate is not None:
                out.append(candidate)
            if len(out) >= limit:
                break
        return out

    def fetch(self, url, dest) -> FetchResult:
        return ManualSource().fetch(url, dest)
