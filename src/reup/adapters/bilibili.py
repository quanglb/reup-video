"""Quét video Bilibili bằng yt-dlp.

Ba đường: từ khoá (`bilisearch` của yt-dlp), `@<uid>` hoặc uid trần (trang
video của kênh `space.bilibili.com/<uid>/video`), và URL dán thẳng. Bilibili
trả 412 cho khách vãng lai, nên gần như luôn cần cookie như TikTok.

Xem thử trong Web UI dùng player nhúng chính chủ của Bilibili.
"""
from __future__ import annotations

import html
import re
import shutil
import tempfile
import time
from pathlib import Path

from reup.adapters.crawl import (
    DiscoverError,
    cookie_args,
    dump_flat,
    duration_ok,
    pick_thumbnail,
)
from reup.adapters.manual import ManualSource
from reup.adapters.source import Candidate, FetchResult

PLATFORM = "bilibili"
DEFAULT_QUERY = "搞笑"


def feed_url(query: str, limit: int = 12) -> str:
    q = (query or DEFAULT_QUERY).strip()
    if q.startswith("http://") or q.startswith("https://"):
        return q
    uid = q.lstrip("@")
    if q.startswith("@") or uid.isdigit():
        return f"https://space.bilibili.com/{uid}/video"
    # bilisearchN: N là số kết quả yt-dlp lấy; lấy dư vì còn lọc theo độ dài.
    return f"bilisearch{limit * 2}:{q}"


def chrome_cookie_dir(browser: str | None, tmp: Path) -> str | None:
    """`--cookies-from-browser chrome` chọn file `Cookies` sửa gần nhất trong
    profile — trên máy này là kho 30 cookie của một tiện ích, không phải kho
    chính, nên Bilibili trả 412. Chép kho chính ra thư mục tạm rồi trỏ vào đó.

    shortcut: chỉ biết đường Chrome trên macOS; trình duyệt khác thì để nguyên.
    """
    if not browser or not browser.startswith("chrome"):
        return browser
    profile = browser.partition(":")[2] or "Default"
    src = Path.home() / "Library/Application Support/Google/Chrome" / profile / "Cookies"
    if not src.exists():
        return browser
    shutil.copy2(src, tmp / "Cookies")
    return f"chrome:{tmp}"


def embed_url(video_id: str) -> str:
    return f"https://player.bilibili.com/player.html?bvid={video_id}&autoplay=0"


def parse_entry(raw: dict) -> Candidate | None:
    video_id = str(raw.get("id") or "").strip()
    if not video_id:
        return None
    duration = raw.get("duration")
    duration_s = float(duration) if duration else 0.0
    if not duration_ok(duration_s):
        return None
    url = raw.get("webpage_url") or raw.get("url")
    if not url or not str(url).startswith("http"):
        url = f"https://www.bilibili.com/video/{video_id}"
    return Candidate(
        platform=PLATFORM,
        video_id=video_id,
        url=str(url),
        title=(raw.get("title") or raw.get("description") or "").strip(),
        duration_ms=round(duration_s * 1000),
        view_count=int(raw.get("view_count") or 0),
        published_at=str(raw.get("upload_date") or ""),
        embed_url=embed_url(video_id) if video_id.startswith("BV") else "",
        thumbnail=pick_thumbnail(raw),
        uploader=raw.get("uploader") or raw.get("channel") or "",
        like_count=int(raw.get("like_count") or 0),
    )


def _clock_s(text: str) -> int:
    """"7:53" hay "1:02:03" ra giây; hỏng thì 0 (cho qua bộ lọc độ dài)."""
    try:
        return sum(int(p) * 60**i for i, p in enumerate(reversed(str(text).split(":"))))
    except ValueError:
        return 0


def parse_search_item(v: dict) -> Candidate | None:
    """Một mục `data.result` của API tìm kiếm. Tiêu đề có thẻ <em> tô từ khoá."""
    bvid = str(v.get("bvid") or "")
    duration_s = _clock_s(v.get("duration") or "")
    if not bvid or not duration_ok(duration_s):
        return None
    pic = str(v.get("pic") or "")
    pubdate = v.get("pubdate") or 0
    return Candidate(
        platform=PLATFORM,
        video_id=bvid,
        url=f"https://www.bilibili.com/video/{bvid}",
        title=html.unescape(re.sub(r"<[^>]+>", "", v.get("title") or "")).strip(),
        duration_ms=duration_s * 1000,
        view_count=int(v.get("play") or 0),
        published_at=time.strftime("%Y%m%d", time.localtime(pubdate)) if pubdate else "",
        embed_url=embed_url(bvid),
        thumbnail="https:" + pic if pic.startswith("//") else pic,
        uploader=str(v.get("author") or ""),
        like_count=int(v.get("like") or 0),
    )


class BilibiliSource:
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
        url = feed_url(self.query)
        if url.startswith("bilisearch"):
            return url, "tìm kiếm"
        return url, "kênh" if "space.bilibili.com" in url else "URL"

    def list_trending(self, region: str, limit: int) -> list[Candidate]:
        with tempfile.TemporaryDirectory() as tmp:
            browser = chrome_cookie_dir(self.cookies_from_browser, Path(tmp))
            if feed_url(self.query).startswith("bilisearch"):
                return self._search(browser, limit)
            return self._scan(cookie_args(browser, self.cookie_file), limit)

    def _search(self, browser: str | None, limit: int) -> list[Candidate]:
        """Tìm qua API có ký wbi, như trang search.bilibili.com gọi.

        `bilisearch` của yt-dlp gọi bản không ký nên hay ăn 412, rồi còn mở
        từng video để lấy tiêu đề — ~25 request một lượt quét, càng dễ bị
        chặn. Kết quả tìm kiếm đã có đủ tiêu đề/ảnh/thời lượng: 1 request/trang.
        Mượn yt-dlp để đọc cookie Chrome và ký wbi.
        """
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import YoutubeDLError

        opts: dict = {"quiet": True, "no_warnings": True}
        if self.cookie_file:
            opts["cookiefile"] = self.cookie_file
        elif browser:
            name, _, profile = browser.partition(":")
            opts["cookiesfrombrowser"] = (name, profile or None, None, None)
        q = (self.query or DEFAULT_QUERY).strip()
        out: list[Candidate] = []
        try:
            ie = YoutubeDL(opts).get_info_extractor("BiliBili")
            for page in range(1, 6):
                # duration=1: Bilibili tự lọc video dưới 10 phút.
                params = {"search_type": "video", "keyword": q, "page": page, "duration": 1}
                data = ie._download_json(
                    "https://api.bilibili.com/x/web-interface/wbi/search/type", q,
                    query=ie._sign_wbi(params, q),
                    headers={"Referer": "https://search.bilibili.com/"},
                )
                if data.get("code"):
                    raise DiscoverError(f"Bilibili trả lỗi {data.get('code')}: {data.get('message')}")
                found = (data.get("data") or {}).get("result") or []
                out += [c for c in map(parse_search_item, found) if c is not None]
                if not found or len(out) >= limit:
                    break
        except YoutubeDLError as exc:
            raise DiscoverError(f"tìm Bilibili hỏng: {exc}") from exc
        return out[:limit]

    def _scan(self, args: list[str], limit: int) -> list[Candidate]:
        try:
            # Quét nhanh (flat) của Bilibili chỉ trả id, không tiêu đề/ảnh.
            entries = dump_flat(
                feed_url(self.query, limit), limit, args, timeout=300, flat=False
            )
        except DiscoverError as exc:
            if not args:
                raise DiscoverError(
                    f"{exc}\n\nBilibili chặn khách vãng lai (lỗi 412). Thêm cookie:\n"
                    "  [discover.bilibili] cookies_from_browser = \"chrome\"\n"
                    "trong config.toml, hoặc dán link thủ công."
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
