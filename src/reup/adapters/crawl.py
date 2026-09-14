"""Phần chung của ba crawler: gọi `yt-dlp --flat-playlist` và đọc từng dòng JSON.

Tách ra vì YouTube, TikTok và Douyin khác nhau ở URL nguồn và cách dựng link,
còn cách quét thì giống hệt nhau. Không tách thì mỗi adapter chép lại một bản
xử lý lỗi riêng, và bản nào cũng sẽ thiếu một nhánh.
"""
from __future__ import annotations

import json
import re
import subprocess

# Spec §3: đích là video dưới ~3 phút, 9:16.
MAX_DURATION_S = 180
MIN_DURATION_S = 5


class DiscoverError(RuntimeError):
    pass


def duration_ok(duration_s: float) -> bool:
    """Độ dài 0 nghĩa là trang liệt kê không khai — cho qua, lọc sau khi tải."""
    return not duration_s or MIN_DURATION_S <= duration_s <= MAX_DURATION_S


def pick_thumbnail(raw: dict) -> str:
    """Ảnh đại diện to nhất. `--flat-playlist` trả mảng `thumbnails` tăng dần
    theo kích thước và thường KHÔNG có khoá `thumbnail` phẳng, nên chỉ đọc
    `thumbnail` là mọi thẻ đều trống ảnh."""
    flat = raw.get("thumbnail")
    if flat:
        return str(flat)
    thumbs = [t for t in (raw.get("thumbnails") or []) if t.get("url")]
    if not thumbs:
        return ""
    return str(max(thumbs, key=lambda t: t.get("width") or 0)["url"])


def dump_flat(
    url: str, limit: int, extra_args: list[str] | None = None, timeout: float = 120.0
) -> list[dict]:
    """Trả danh sách entry thô. Ném DiscoverError khi yt-dlp không ra gì.

    yt-dlp hay trả mã khác 0 kèm cảnh báo nhưng vẫn in được entry; chỉ coi là
    hỏng khi stdout rỗng thật.
    """
    if limit <= 0:
        raise ValueError(f"limit phải dương, nhận {limit}")
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--ignore-errors",
        "--playlist-end",
        str(limit * 2),  # lấy dư vì sẽ lọc theo độ dài
        *(extra_args or []),
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise DiscoverError("không tìm thấy yt-dlp trong PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise DiscoverError(f"yt-dlp quá {timeout:.0f}s không trả lời: {url}") from exc

    if not proc.stdout.strip():
        raise DiscoverError(
            f"yt-dlp không quét được {url} (mã {proc.returncode}).\n"
            f"stderr:\n{proc.stderr[-800:]}"
        )

    out: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# Nền tảng không nhận tham số sắp xếp qua yt-dlp, nên sắp tại chỗ.
def _date_key(c) -> int:
    """"20250912" hay "2025-09-12" đều về 20250912. Không có ngày thì xếp cuối."""
    digits = re.sub(r"\D", "", str(c.published_at or ""))[:8]
    return -int(digits) if len(digits) == 8 else 0


SORTS = {
    "": ("thứ tự trang", None),
    "views": ("lượt xem", lambda c: -c.view_count),
    "likes": ("lượt thích", lambda c: -c.like_count),
    "newest": ("mới nhất", _date_key),
    "short": ("ngắn nhất", lambda c: c.duration_ms or 10**9),
}


def order_and_slice(found: list, sort: str, start: int, limit: int) -> list:
    """Sắp rồi mới cắt: "từ vị trí 5, lấy 10 video nhiều like nhất" phải xếp
    hạng trên cả danh sách, không phải trên 10 video đầu kênh.

    `start` đếm từ 1 trên danh sách ĐÃ sắp — để trống sắp xếp thì trùng với vị
    trí trên kênh, như script "tải từ vị trí mong muốn" trong douyin-doc.
    """
    if sort not in SORTS:
        raise ValueError(f"không có kiểu sắp xếp {sort!r}. Chọn: {sorted(SORTS)}")
    if start < 1:
        raise ValueError(f"vị trí bắt đầu phải từ 1, nhận {start}")
    key = SORTS[sort][1]
    ordered = sorted(found, key=key) if key is not None else list(found)
    return ordered[start - 1 : start - 1 + max(1, limit)]


def cookie_args(browser: str | None, cookie_file: str | None) -> list[str]:
    """TikTok và Douyin chặn khách vãng lai; cookie là đường duy nhất qua được."""
    if cookie_file:
        return ["--cookies", cookie_file]
    if browser:
        return ["--cookies-from-browser", browser]
    return []
