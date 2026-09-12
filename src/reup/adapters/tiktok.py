"""Quét video TikTok bằng yt-dlp.

TikTok không có trang trending mở cho khách. Hai đường còn sống là trang
hashtag (`/tag/<từ khoá>`) và trang người dùng (`/@tên`). Cả hai thường đòi
cookie: không có cookie thì TikTok trả trang chặn và yt-dlp không ra entry nào.
Vì vậy `list_trending` nhận `cookies_from_browser` / `cookie_file`; thiếu thì
vẫn thử, và khi hỏng thì DiscoverError nói thẳng là thiếu cookie.

Xem thử trong Web UI dùng player nhúng chính chủ:
`https://www.tiktok.com/embed/v2/<id>`.
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

PLATFORM = "tiktok"
DEFAULT_QUERY = "xuhuong"


def feed_url(query: str) -> str:
    """`query` là hashtag trần, `@tên` người dùng, hoặc nguyên một URL."""
    q = (query or DEFAULT_QUERY).strip()
    if q.startswith("http://") or q.startswith("https://"):
        return q
    if q.startswith("@"):
        return f"https://www.tiktok.com/{q}"
    return f"https://www.tiktok.com/tag/{q.lstrip('#')}"


def embed_url(video_id: str) -> str:
    return f"https://www.tiktok.com/embed/v2/{video_id}"


def parse_entry(raw: dict) -> Candidate | None:
    video_id = str(raw.get("id") or "").strip()
    if not video_id:
        return None
    duration = raw.get("duration")
    duration_s = float(duration) if duration else 0.0
    if not duration_ok(duration_s):
        return None
    uploader = (raw.get("uploader") or raw.get("channel") or "").lstrip("@")
    # yt-dlp trả sẵn webpage_url cho trang hashtag; trang người dùng thì không.
    url = raw.get("url") or raw.get("webpage_url")
    if not url or not str(url).startswith("http"):
        who = uploader or "user"
        url = f"https://www.tiktok.com/@{who}/video/{video_id}"
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
        uploader=uploader,
    )


class TikTokSource:
    name = PLATFORM

    def __init__(
        self,
        query: str = DEFAULT_QUERY,
        cookies_from_browser: str | None = None,
        cookie_file: str | None = None,
    ) -> None:
        self.query = query
        self.cookies_from_browser = cookies_from_browser
        self.cookie_file = cookie_file

    def describe(self) -> tuple[str, str]:
        q = (self.query or DEFAULT_QUERY).strip()
        if q.startswith("http"):
            kind = "URL"
        elif q.startswith("@"):
            kind = "kênh"
        else:
            kind = "hashtag"
        return feed_url(self.query), kind

    def list_trending(self, region: str, limit: int) -> list[Candidate]:
        args = cookie_args(self.cookies_from_browser, self.cookie_file)
        try:
            entries = dump_flat(feed_url(self.query), limit, args)
        except DiscoverError as exc:
            if not args:
                raise DiscoverError(
                    f"{exc}\n\nTikTok gần như luôn đòi đăng nhập. Thêm cookie:\n"
                    "  [discover.tiktok] cookies_from_browser = \"chrome\"\n"
                    "trong config.toml, hoặc dán link thủ công bằng `reup add <url>`."
                ) from exc
            raise

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
