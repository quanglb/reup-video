"""Đọc file JSON xuất từ console Douyin, và tải video thẳng từ `play_addr`.

Vì sao không gọi API từ Python: `/aweme/v1/web/aweme/post/` trả 200 với body
rỗng cho request không mang cookie đăng nhập của trang. Các script trong
`douyin-doc/` chạy được chính vì chúng chạy trong tab đã đăng nhập. Nên chia đôi
việc: trình duyệt lấy danh sách (`web/static/douyin_export.js`), reup đọc file
đó để sắp theo like, cắt theo vị trí, và tải.

`play_addr` là link CDN có ký, hết hạn sau vài giờ. Hết hạn thì stage `fetch`
rơi về yt-dlp với link trang video — đường đó cần cookie và IP Trung Quốc, nên
xuất xong hãy chọn video sớm.
"""
from __future__ import annotations

import json
import shutil
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from reup.adapters.crawl import duration_ok
from reup.adapters.douyin import PLATFORM, embed_url
from reup.adapters.source import Candidate

FORMAT = "reup-douyin/1"
DIRECT_FILE = "source.direct.json"
REFERER = "https://www.douyin.com/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def is_export(query: str) -> bool:
    return (query or "").strip().lower().endswith(".json")


def video_url(aweme_id: str) -> str:
    return f"https://www.douyin.com/video/{aweme_id}"


def _https(url: str) -> str:
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


def _date(ts) -> str:
    """Cùng dạng `upload_date` của yt-dlp (YYYYMMDD) để UI không phải biết hai kiểu."""
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y%m%d")
    except (TypeError, ValueError, OSError):
        return ""


def parse_export(raw: dict) -> list[Candidate]:
    """Giữ nguyên thứ tự trên kênh. Bỏ bài không có link video và video ngoài
    khoảng 5s–3 phút, giống ba crawler kia."""
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ValueError(
            f"không phải file xuất từ script Douyin của reup (cần format = {FORMAT!r}). "
            "Chạy lại script ở tab Douyin rồi nạp file mới"
        )
    out: list[Candidate] = []
    for v in raw.get("videos") or []:
        aweme_id = str(v.get("aweme_id") or "").strip()
        media = _https(str(v.get("play_url") or "").strip())
        if not aweme_id or not media.startswith("https://"):
            continue
        duration_ms = int(v.get("duration_ms") or 0)
        if not duration_ok(duration_ms / 1000):
            continue
        out.append(
            Candidate(
                platform=PLATFORM,
                video_id=aweme_id,
                url=video_url(aweme_id),
                title=str(v.get("desc") or "").strip(),
                duration_ms=duration_ms,
                view_count=int(v.get("play_count") or 0),
                published_at=_date(v.get("create_time")),
                embed_url=embed_url(aweme_id),
                thumbnail=_https(str(v.get("cover") or "")),
                uploader=str(v.get("author") or raw.get("author") or "").strip(),
                like_count=int(v.get("digg_count") or 0),
                share_count=int(v.get("share_count") or 0),
                position=int(v.get("position") or 0),
                media_url=media,
            )
        )
    return out


def load_export(path: Path) -> list[Candidate]:
    path = Path(path)
    if not path.exists():
        raise ValueError(f"không thấy file xuất Douyin: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path.name} không phải JSON hợp lệ: {exc}") from exc
    return parse_export(raw)


def recent_exports(folder: Path, limit: int = 8) -> list[dict]:
    """File đã nạp, mới nhất trước — làm gợi ý để quét lại khỏi phải nạp lần nữa.

    File hỏng hoặc không phải file xuất thì bỏ qua: gợi ý hỏng còn tệ hơn không có.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict] = []
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            count = len(parse_export(raw))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        who = str(raw.get("author") or "").strip() or str(raw.get("sec_user_id") or "")[-10:]
        when = str(raw.get("exported_at") or "")[:10] or datetime.fromtimestamp(
            path.stat().st_mtime
        ).strftime("%Y-%m-%d")
        out.append(
            {"path": str(path.resolve()), "author": who or path.stem, "count": count, "date": when}
        )
        if len(out) >= limit:
            break
    return out


# Douyin không có trang hashtag mở, nhưng trang tìm kênh thì có. Mở ở tab mới,
# người dùng chọn kênh rồi chạy script — không phải gõ tiếng Trung.
SEARCH_TOPICS: list[tuple[str, str]] = [
    ("美食 制作", "Nấu ăn"),
    ("搞笑", "Hài hước"),
    ("短剧", "Phim ngắn"),
    ("乡村 生活", "Thôn quê"),
    ("手工 制作", "Thủ công/DIY"),
    ("修车", "Sửa xe"),
    ("街头 采访", "Phỏng vấn"),
    ("萌宠", "Thú cưng"),
    ("科普", "Kiến thức"),
    ("开箱 测评", "Review"),
]


def search_url(keyword: str) -> str:
    from urllib.parse import quote

    return f"https://www.douyin.com/search/{quote(keyword)}?type=user"


def save_direct(
    root: Path,
    media_url: str,
    *,
    video_id: str = "",
    url: str = "",
    title: str = "",
    uploader: str = "",
    duration_ms: int = 0,
    like_count: int = 0,
    share_count: int = 0,
) -> Path:
    """Ghi link tải thẳng vào thư mục job để stage `fetch` dùng thay yt-dlp."""
    media_url = _https((media_url or "").strip())
    if not media_url.startswith("https://"):
        raise ValueError(f"link tải Douyin phải là https, nhận {media_url!r}")
    out = Path(root) / DIRECT_FILE
    out.write_text(
        json.dumps(
            {
                "media_url": media_url,
                "video_id": video_id,
                "url": url,
                "title": title,
                "uploader": uploader,
                "duration_ms": duration_ms,
                "like_count": like_count,
                "share_count": share_count,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def download(url: str, dest: Path, timeout: float = 120.0) -> None:
    """CDN Douyin chặn request thiếu Referer; ghi ra file tạm rồi mới đổi tên để
    một lần tải đứt giữa chừng không để lại source.mp4 nửa vời."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": REFERER})
    tmp = Path(dest).with_suffix(".part")
    with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as f:
        shutil.copyfileobj(resp, f)
    if tmp.stat().st_size == 0:
        tmp.unlink()
        raise OSError(f"CDN Douyin trả file rỗng: {url}")
    tmp.replace(dest)


def fetch_direct(root: Path, dest: Path, info_path: Path) -> bool:
    """Tải theo `source.direct.json` nếu job có. False khi job không từ file xuất."""
    meta_path = Path(root) / DIRECT_FILE
    if not meta_path.exists():
        return False
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    download(meta["media_url"], dest)
    # Khoá theo kiểu yt-dlp: translate đoán thể loại và export dựng metadata từ đây.
    info_path.write_text(
        json.dumps(
            {
                "id": meta.get("video_id", ""),
                "title": meta.get("title", ""),
                "description": meta.get("title", ""),
                "uploader": meta.get("uploader", ""),
                "duration": (meta.get("duration_ms") or 0) / 1000,
                "like_count": meta.get("like_count", 0),
                "repost_count": meta.get("share_count", 0),
                "webpage_url": meta.get("url", ""),
                "extractor": "douyin",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return True
