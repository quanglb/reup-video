"""Đang trend / evergreen trên các kênh Douyin đã lưu.

`play_count` trong file xuất Douyin luôn = 0 (nền tảng không lộ view công
khai), nên dùng like + share làm proxy engagement. Không so kênh với kênh:
kênh to vốn có số tuyệt đối cao dù chẳng có gì bất thường đang xảy ra, nên mỗi
video được so lệch (z-score) với velocity trung vị CỦA CHÍNH kênh đó.

Hai nhóm khác hẳn nhau, không dùng chung một ngưỡng:
- Đang trend: video mới, velocity vọt lên bất thường — bắt kịp thời, làm chậm
  là mất giá trị.
- Evergreen: video cũ nhưng vẫn được thích nhiều — công thức nội dung ổn định,
  làm lúc nào cũng được, không sợ trễ trend.
"""
from __future__ import annotations

import statistics
from datetime import date
from pathlib import Path

from reup.adapters.douyin_export import load_export
from reup.adapters.source import Candidate

TREND_MAX_AGE_DAYS = 21
EVERGREEN_MIN_AGE_DAYS = 365
MIN_VIDEOS_FOR_STATS = 5  # kênh quá ít video thì z-score không có nghĩa
Z_THRESHOLD = 1.0
TOP_N = 15


def _age_days(published_at: str) -> float | None:
    if len(published_at) != 8:
        return None
    try:
        d = date(int(published_at[:4]), int(published_at[4:6]), int(published_at[6:8]))
    except ValueError:
        return None
    return max((date.today() - d).days, 0) + 0.5  # +0.5 tránh chia 0 cho video hôm nay


def _row(c: Candidate, author: str, age_days: float, z: float) -> dict:
    return {
        "platform": c.platform,
        "video_id": c.video_id,
        "url": c.url,
        "embed_url": c.embed_url,
        "title": c.title or c.video_id,
        "uploader": c.uploader or author,
        "thumbnail": c.thumbnail,
        "duration_ms": c.duration_ms,
        "published_at": c.published_at,
        "media_url": c.media_url,
        "age_days": round(age_days),
        "like_count": c.like_count,
        "share_count": c.share_count,
        "share_ratio": round(c.share_count / max(c.like_count, 1), 2),
        "z": round(z, 2),
    }


def analyze(export_paths: list[str], author_by_path: dict[str, str] | None = None) -> dict:
    """`export_paths`: bản xuất mới nhất của mỗi kênh đã lưu.

    Trả `{"trending": [...], "evergreen": [...]}`, mỗi phần tử đủ trường để vẽ
    lại thành thẻ chọn video (giống `CandidateRow`), sắp theo mức bất thường
    (trend) hoặc lượt thích (evergreen).
    """
    author_by_path = author_by_path or {}
    trending: list[dict] = []
    evergreen: list[dict] = []

    for path in export_paths:
        try:
            candidates = load_export(Path(path))
        except ValueError:
            continue
        author = author_by_path.get(path, "")

        aged: list[tuple[Candidate, float, float]] = []
        for c in candidates:
            age = _age_days(c.published_at)
            if age is None:
                continue
            engagement = c.like_count + c.share_count * 3
            aged.append((c, age, engagement / age))
        if len(aged) < MIN_VIDEOS_FOR_STATS:
            continue

        velocities = [v for _, _, v in aged]
        median_v = statistics.median(velocities)
        stdev_v = statistics.pstdev(velocities) or 1.0

        for c, age, velocity in aged:
            z = (velocity - median_v) / stdev_v
            if z <= Z_THRESHOLD:
                continue
            if age <= TREND_MAX_AGE_DAYS:
                trending.append(_row(c, author, age, z))
            elif age > EVERGREEN_MIN_AGE_DAYS:
                evergreen.append(_row(c, author, age, z))

    trending.sort(key=lambda r: r["z"], reverse=True)
    evergreen.sort(key=lambda r: r["like_count"], reverse=True)
    return {"trending": trending[:TOP_N], "evergreen": evergreen[:TOP_N]}
