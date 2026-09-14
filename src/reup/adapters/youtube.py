"""Quét video ngắn trên YouTube bằng yt-dlp.

`/feed/trending` đã chết — yt-dlp báo "channel/playlist does not exist and the
URL redirected to youtube.com home page". Bốn đường còn sống, phân biệt bằng ký
tự đầu của `query` (xem `feed_url`): tìm kiếm, hashtag, kênh, và URL dán thẳng.

Tìm kiếm phải kèm bộ lọc thời lượng của chính YouTube. Đo thật với "mèo hài":
không lọc thì bốn kết quả đầu dài 638s, 940s, 515s — bộ lọc 3 phút của mình
quét sạch, trang ra rỗng dù YouTube trả đủ dữ liệu. Kèm `sp=EgIYAQ==` thì bốn
kết quả đầu còn 113s, 47s, 6s, 103s.

Đây là adapter chạy được mà không cần cookie. TikTok và Douyin cần cookie và
(với Douyin) IP Trung Quốc — xem `tiktok.py`, `douyin.py`. Adapter `manual` luôn
sống, nên không có crawler nào thì pipeline vẫn chạy được bằng cách dán link.
"""
from __future__ import annotations

from reup.adapters.crawl import (  # noqa: F401 — giữ tên cũ cho chỗ đang import
    MAX_DURATION_S,
    MIN_DURATION_S,
    DiscoverError,
    dump_flat,
    pick_thumbnail,
)
from reup.adapters.source import Candidate, FetchResult
from reup.adapters.manual import ManualSource

PLATFORM = "youtube"
DEFAULT_QUERY = "#shorts"

# Bộ lọc "dưới 4 phút" của trang kết quả YouTube. Chuỗi đục nhưng là của
# YouTube, không phải mình bịa: `sp` là bộ lọc đã mã hoá, EgIYAQ== là thời
# lượng ngắn. Không có nó thì tìm kiếm chỉ trả video dài.
SEARCH_SHORT_FILTER = "EgIYAQ%3D%3D"


def embed_url(video_id: str) -> str:
    return f"https://www.youtube.com/embed/{video_id}"


def feed_url(region: str, query: str = DEFAULT_QUERY) -> str:
    """Đổi `query` người dùng gõ thành URL nguồn.

        https://...     -> dùng nguyên si
        @tên            -> tab Shorts của kênh
        #tag            -> trang hashtag
        chữ thường      -> tìm kiếm, kèm bộ lọc dưới 4 phút

    Chữ trần là tìm kiếm chứ không phải hashtag: gõ "mèo hài" vào ô tìm mà ra
    trang hashtag rỗng thì không ai đoán được vì sao.

    `region` chưa dùng được: không đường nào trong bốn đường nhận tham số vùng.
    Giữ tham số cho đúng interface `SourceAdapter`.
    """
    from urllib.parse import quote_plus

    q = (query or DEFAULT_QUERY).strip()
    if q.startswith("http://") or q.startswith("https://"):
        return q
    if q.startswith("@"):
        return f"https://www.youtube.com/{q}/shorts"
    if q.startswith("#"):
        return f"https://www.youtube.com/hashtag/{quote_plus(q.lstrip('#'))}"
    return (
        "https://www.youtube.com/results"
        f"?search_query={quote_plus(q)}&sp={SEARCH_SHORT_FILTER}"
    )


def query_kind(query: str) -> str:
    """Nhãn cho UI: người dùng phải thấy ô mình gõ được hiểu thành gì."""
    q = (query or DEFAULT_QUERY).strip()
    if q.startswith("http://") or q.startswith("https://"):
        return "URL"
    if q.startswith("@"):
        return "kênh"
    if q.startswith("#"):
        return "hashtag"
    return "tìm kiếm"


def parse_entry(raw: dict, fallback_uploader: str = "") -> Candidate | None:
    """`fallback_uploader`: tab Shorts của kênh không khai `uploader` trong từng
    entry (đo thật trên `@MrBeast/shorts`), nhưng tên kênh thì nằm sẵn ở query."""
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
        embed_url=embed_url(video_id),
        thumbnail=pick_thumbnail(raw),
        uploader=(
            raw.get("uploader") or raw.get("channel") or fallback_uploader
        ).strip(),
    )


class YouTubeSource:
    name = PLATFORM

    def __init__(self, query: str = DEFAULT_QUERY) -> None:
        self.query = query or DEFAULT_QUERY

    def describe(self) -> tuple[str, str]:
        """(URL thật sẽ quét, nhãn loại nguồn) — để UI nói ra mình hiểu gì."""
        return feed_url("", self.query), query_kind(self.query)

    def list_trending(self, region: str, limit: int) -> list[Candidate]:
        try:
            entries = dump_flat(feed_url(region, self.query), limit)
        except DiscoverError as exc:
            raise DiscoverError(
                f"{exc}\n\nYouTube hay đổi cấu trúc trang; dán link thủ công bằng "
                "`reup add <url>` trong lúc chờ sửa."
            ) from exc

        channel = self.query.lstrip("@") if self.query.startswith("@") else ""
        out: list[Candidate] = []
        for raw in entries:
            candidate = parse_entry(raw, fallback_uploader=channel)
            if candidate is not None:
                out.append(candidate)
            if len(out) >= limit:
                break
        return out

    def fetch(self, url, dest) -> FetchResult:
        # Tải về vẫn là việc của yt-dlp, giống hệt nguồn thủ công.
        return ManualSource().fetch(url, dest)
