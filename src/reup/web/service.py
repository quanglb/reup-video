"""Đọc ghi trạng thái cho Web UI. Không chứa logic xử lý video.

Tách khỏi `app.py` để test được mà không cần dựng HTTP.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from reup.config import Config
from reup.core.job import Job, load_job
from reup.core.store import Store
from reup.models import Transcript
from reup.text import count_syllables
from reup.translate import syllable_budget


@dataclass(frozen=True)
class JobRow:
    id: str
    url: str
    status: str
    stage: str
    error: str


def list_jobs(store: Store, status: str | None = None) -> list[JobRow]:
    return [
        JobRow(
            id=r["id"],
            url=r["url"],
            status=r["status"],
            stage=r["stage"] or "-",
            error=r["error"] or "",
        )
        for r in store.list_jobs(status)
    ]


def review_rows(job: Job) -> list[dict]:
    """Bảng từng câu cho chốt A: giờ, chữ gốc, bản dịch, ngân sách, cờ."""
    if not job.translation_json.exists():
        return []
    raw = json.loads(job.translation_json.read_text(encoding="utf-8"))
    source = {}
    if job.transcript_json.exists():
        source = {
            s.id: s.text for s in Transcript.load(job.transcript_json).segments
        }

    rows = []
    for s in raw["segments"]:
        budget = s.get("syllable_budget") or syllable_budget(s["slot_ms"])
        syllables = count_syllables(s["text"], "vi")
        rows.append(
            {
                "id": s["id"],
                "start_ms": s["start_ms"],
                "end_ms": s["end_ms"],
                "slot_ms": s["slot_ms"],
                "source_text": source.get(s["id"], ""),
                "text": s["text"],
                "syllables": syllables,
                "budget": budget,
                "over": syllables > budget,
                "flags": s.get("flags", []),
                "revision": s.get("revision", 0),
            }
        )
    return rows


def save_edits(job: Job, edits: dict[int, str]) -> int:
    """Ghi bản dịch người dùng sửa. Trả số câu thật sự đổi.

    Câu nào đổi chữ thì XOÁ file TTS của nó, để lần chạy sau tổng hợp lại.
    Không xoá thì giọng đọc vẫn là câu cũ trong khi phụ đề đã là câu mới.
    """
    raw = json.loads(job.translation_json.read_text(encoding="utf-8"))
    changed = 0
    for s in raw["segments"]:
        new_text = (edits.get(s["id"]) or "").strip()
        if not new_text or new_text == s["text"]:
            continue
        s["text"] = new_text
        s["syllables"] = count_syllables(new_text, "vi")
        budget = s.get("syllable_budget") or syllable_budget(s["slot_ms"])
        s["flags"] = [f for f in s.get("flags", []) if f != "over_budget"]
        if s["syllables"] > budget:
            s["flags"].append("over_budget")
        s["text_source"] = "human"
        changed += 1
        job.tts_segment(s["id"]).unlink(missing_ok=True)

    if changed:
        from reup.core.runner import atomic_write

        atomic_write(
            job.translation_json, json.dumps(raw, ensure_ascii=False, indent=2)
        )
        # Xoá cả manifest, không chỉ file wav của câu đã sửa: artifact khai báo
        # của stage tts là manifest.json, nên còn manifest là tts bị bỏ qua và
        # fit sẽ đi tìm một file wav không còn tồn tại.
        #
        # Tổng hợp lại cả loạt nghe có vẻ phí, nhưng server CapCut cache theo
        # text: những câu không sửa trả về tức thì và không tốn lượt gọi nào.
        (job.tts_dir / "manifest.json").unlink(missing_ok=True)
        job.dub_wav.unlink(missing_ok=True)
        job.sub_ass.unlink(missing_ok=True)
        job.final_mp4.unlink(missing_ok=True)
    return changed


def voices_for(cfg: Config) -> list[dict]:
    from reup.adapters.registry import make_tts

    try:
        return [
            {"id": v.id, "name": v.name} for v in make_tts(cfg).voices("vi")
        ]
    except Exception:
        return []


def open_job(jobs_dir: Path, job_id: str) -> Job:
    return load_job(jobs_dir, job_id)


def pick_voice(job: Job, cfg: Config) -> str:
    """Giọng đang hiệu lực cho job: lựa chọn ở chốt A, nếu không thì config."""
    return job.overrides.get("voice") or cfg.tts.voice


def set_voice(job: Job, voice: str, cfg: Config) -> bool:
    """Chọn giọng cho cả job. Trả True khi thật sự đổi.

    Đổi giọng làm mọi file wav đã tổng hợp thành sai giọng, nên phải dọn hết
    như `save_edits` dọn câu đã sửa. Server CapCut cache theo *text*, không theo
    giọng, nên tổng hợp lại cả loạt với giọng mới là tốn lượt gọi thật — vì vậy
    chỉ dọn khi giọng đổi.
    """
    voice = (voice or "").strip()
    if not voice:
        raise ValueError("chưa chọn giọng")
    known = {v["id"] for v in voices_for(cfg)}
    if known and voice not in known:
        raise ValueError(f"giọng {voice!r} không có trong Voice.json")
    if voice == pick_voice(job, cfg):
        return False

    job.set_override("voice", voice)
    for wav in sorted(job.tts_dir.glob("seg_*.wav")):
        wav.unlink()
    for wav in sorted(job.preview_dir.glob("seg_*.wav")):
        wav.unlink()
    (job.tts_dir / "manifest.json").unlink(missing_ok=True)
    job.dub_wav.unlink(missing_ok=True)
    job.final_mp4.unlink(missing_ok=True)
    return True


def preview_audio(job: Job, cfg: Config, seg_id: int) -> Path:
    """File wav để nghe thử một câu ở chốt A.

    Ở chốt A stage `tts` chưa chạy, nên phần lớn thời gian chưa có file nào.
    Tổng hợp đúng một câu — đủ để nghe giọng và nhịp, không tốn cả loạt.

    Có sẵn file thật của stage `tts` thì dùng luôn: cùng giọng, cùng chữ.
    """
    done = job.tts_segment(seg_id)
    if done.exists():
        return done

    rows = {r["id"]: r for r in review_rows(job)}
    if seg_id not in rows:
        raise KeyError(f"job {job.id} không có câu {seg_id}")

    from reup.adapters.registry import make_tts

    job.preview_dir.mkdir(parents=True, exist_ok=True)
    out = job.preview_segment(seg_id)
    make_tts(cfg).synthesize(
        rows[seg_id]["text"], "vi", pick_voice(job, cfg), out
    )
    return out


# --- tab quét nguồn ---------------------------------------------------------

PLATFORM_LABELS = {
    "youtube": "YouTube Shorts",
    "tiktok": "TikTok",
    "douyin": "Douyin",
}


@dataclass(frozen=True)
class CandidateRow:
    platform: str
    video_id: str
    url: str
    embed_url: str
    title: str
    uploader: str
    thumbnail: str
    duration_ms: int
    view_count: int
    published_at: str
    seen: bool
    job_id: str


def duration_label(ms: int) -> str:
    if ms <= 0:
        return "?"
    total = ms // 1000
    return f"{total // 60}:{total % 60:02d}"


def view_label(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f} tỷ"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n or "")


SORTS = {
    "": ("thứ tự trang", None),
    "views": ("lượt xem", lambda c: -c.view_count),
    "short": ("ngắn nhất", lambda c: c.duration_ms or 10**9),
}


@dataclass(frozen=True)
class ScanResult:
    rows: list[CandidateRow]
    feed_url: str
    kind: str
    hidden: int  # số video bị ẩn vì đã xử lý rồi


def discover(
    store: Store,
    cfg: Config,
    platform: str,
    query: str = "",
    limit: int = 12,
    sort: str = "",
    hide_seen: bool = False,
) -> ScanResult:
    """Quét một nền tảng và đánh dấu video nào đã xử lý rồi.

    Không tự tạo job: người dùng xem iframe rồi chọn. Ném DiscoverError để
    tầng HTTP hiện đúng lời crawler nói — cái thiếu thường là cookie, và câu
    "quét hỏng" trơn thì không sửa được gì.

    Sắp xếp ở đây chứ không nhờ nền tảng: cả ba đều không nhận tham số sắp xếp
    qua yt-dlp, và một trang kết quả thì đủ nhỏ để sắp tại chỗ.
    """
    from reup.adapters.registry import make_source

    if sort not in SORTS:
        raise ValueError(f"không có kiểu sắp xếp {sort!r}. Chọn: {sorted(SORTS)}")

    source = make_source(cfg, platform, query)
    found = source.list_trending("VN", max(1, limit))
    feed, kind = source.describe()
    by_url = {r["url"]: r["id"] for r in store.list_jobs()}

    key = SORTS[sort][1]
    if key is not None:
        found = sorted(found, key=key)

    rows = [
        CandidateRow(
            platform=c.platform,
            video_id=c.video_id,
            url=c.url,
            embed_url=c.embed_url,
            title=c.title or c.video_id,
            uploader=c.uploader,
            thumbnail=c.thumbnail,
            duration_ms=c.duration_ms,
            view_count=c.view_count,
            published_at=c.published_at,
            seen=store.is_seen(c.platform, c.video_id),
            job_id=by_url.get(c.url, ""),
        )
        for c in found
    ]
    kept = [r for r in rows if not (r.seen or r.job_id)] if hide_seen else rows
    return ScanResult(
        rows=kept, feed_url=feed, kind=kind, hidden=len(rows) - len(kept)
    )
