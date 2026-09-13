"""Đọc ghi trạng thái cho Web UI. Không chứa logic xử lý video.

Tách khỏi `app.py` để test được mà không cần dựng HTTP.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from reup.adapters.crawl import SORTS, order_and_slice
from reup.config import Config
from reup.core.job import Job, load_job
from reup.core.store import Store
from reup.models import Transcript
from reup.text import count_syllables
from reup.translate import syllable_budget


STAGE_LABELS: dict[str, str] = {
    "fetch": "Tải video nguồn",
    "demux": "Tách luồng âm thanh",
    "separate": "Tách nhạc nền (Demucs)",
    "asr": "Nhận diện giọng nói (Whisper)",
    "subdetect": "Dò vùng phụ đề gốc",
    "ocr": "Đọc chữ phụ đề (Apple Vision)",
    "reconcile": "Hợp nhất ASR + OCR",
    "translate": "Dịch sang tiếng Việt",
    "gate_a": "Chờ duyệt bản dịch (Chốt A)",
    "tts": "Tạo giọng đọc (CapCut TTS)",
    "fit": "Khớp câu & nhịp độ (LLM Local)",
    "compose": "Render video & chèn phụ đề",
    "gate_b": "Chờ duyệt video thành phẩm (Chốt B)",
    "export": "Tạo tiêu đề/hashtag & xuất bản",
}


@dataclass(frozen=True)
class JobRow:
    id: str
    url: str
    status: str
    stage: str
    stage_label: str
    percent: int
    error: str


def calculate_progress(
    status: str, stage: str | None, cfg: Config | None = None
) -> tuple[int, str]:
    """Tính % tiến trình và tên mô tả tiếng Việt cho stage/status hiện tại."""
    from reup.stages import ALL_STAGES, stages_for

    stages = [s.name for s in (stages_for(cfg) if cfg else ALL_STAGES)]
    total = len(stages) or 12

    if status == "done":
        return 100, "Hoàn tất 100%"

    if not stage or stage == "-":
        if status == "pending":
            return 0, "Đang chờ chạy"
        return 0, status

    if stage == "gate_a":
        t_idx = stages.index("translate") if "translate" in stages else 7
        pct = round(((t_idx + 1) / total) * 100)
        return pct, STAGE_LABELS.get("gate_a", "Chờ duyệt bản dịch (Chốt A)")
    if stage == "gate_b":
        c_idx = stages.index("compose") if "compose" in stages else 10
        pct = round(((c_idx + 1) / total) * 100)
        return pct, STAGE_LABELS.get("gate_b", "Chờ duyệt thành phẩm (Chốt B)")

    clean_stage = stage.removeprefix("gate_")
    if clean_stage in stages:
        idx = stages.index(clean_stage)
        if status == "running":
            pct = max(5, round(((idx + 0.5) / total) * 100))
        elif status == "failed":
            pct = round((idx / total) * 100)
        elif status == "needs_review":
            pct = round(((idx + 1) / total) * 100)
        else:
            pct = round((idx / total) * 100)
        label = STAGE_LABELS.get(clean_stage, clean_stage)
        return min(99, pct), label

    return 0, STAGE_LABELS.get(stage, stage)


def list_jobs(
    store: Store, status: str | None = None, cfg: Config | None = None
) -> list[JobRow]:
    rows = []
    for r in store.list_jobs(status):
        pct, label = calculate_progress(r["status"], r["stage"], cfg)
        rows.append(
            JobRow(
                id=r["id"],
                url=r["url"],
                status=r["status"],
                stage=r["stage"] or "-",
                stage_label=label,
                percent=pct,
                error=r["error"] or "",
            )
        )
    return rows


def get_job_progress(job: Job, row: dict | None, cfg: Config) -> dict:
    """Chi tiết tiến trình và nhật ký cho từng job."""
    from reup.stages import stages_for

    specs = stages_for(cfg)
    status = row["status"] if row else ("done" if job.final_mp4.exists() else "pending")
    stage = row["stage"] if row else None
    pct, stage_label = calculate_progress(status, stage, cfg)

    logs = []
    log_map: dict[str, dict] = {}
    if job.log_jsonl.exists():
        try:
            for line in job.log_jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                st = entry.get("stage", "")
                label = STAGE_LABELS.get(st, st)
                started = entry.get("started", 0)
                finished = entry.get("finished", 0)
                dur = round(finished - started, 1) if (finished and started) else 0.0
                entry_data = {
                    "stage": st,
                    "label": label,
                    "ok": entry.get("ok", True),
                    "started": started,
                    "finished": finished,
                    "duration_s": dur,
                    "error": entry.get("error", ""),
                }
                logs.append(entry_data)
                log_map[st] = entry_data
        except Exception:
            pass

    stage_list = []
    current_found = False
    cur_clean = (stage or "").removeprefix("gate_")

    for i, s in enumerate(specs):
        s_name = s.name
        s_label = STAGE_LABELS.get(s_name, s_name)
        log_entry = log_map.get(s_name)

        if status == "done":
            s_status = "done"
        elif s_name == cur_clean and status == "running":
            s_status = "running"
            current_found = True
        elif s_name == cur_clean and status == "failed":
            s_status = "failed"
            current_found = True
        elif stage == "gate_a" and s_name == "translate":
            s_status = "needs_review"
        elif stage == "gate_b" and s_name == "compose":
            s_status = "needs_review"
        elif log_entry and log_entry["ok"]:
            s_status = "done"
        elif not current_found and any((job.root / p).exists() for p in s.produces):
            s_status = "done"
        else:
            s_status = "waiting"

        stage_list.append(
            {
                "name": s_name,
                "label": s_label,
                "step": i + 1,
                "status": s_status,
                "duration_s": log_entry.get("duration_s") if log_entry else None,
                "error": log_entry.get("error") if log_entry else None,
            }
        )

    return {
        "id": job.id,
        "status": status,
        "stage": stage or "-",
        "stage_label": stage_label,
        "percent": pct,
        "total_stages": len(specs),
        "stages": stage_list,
        "logs": logs,
    }


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
    like_count: int = 0
    share_count: int = 0
    position: int = 0
    media_url: str = ""


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


@dataclass(frozen=True)
class ScanResult:
    rows: list[CandidateRow]
    feed_url: str
    kind: str
    hidden: int  # số video bị ẩn vì đã xử lý rồi
    total: int = 0  # số video nguồn trả về trước khi cắt theo vị trí


def discover(
    store: Store,
    cfg: Config,
    platform: str,
    query: str = "",
    limit: int = 12,
    sort: str = "",
    hide_seen: bool = False,
    start: int = 1,
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

    if start < 1:
        raise ValueError(f"vị trí bắt đầu phải từ 1, nhận {start}")

    source = make_source(cfg, platform, query)
    # Crawler yt-dlp chỉ lấy đủ tới vị trí cần; file xuất thì đọc cả kênh.
    want = start - 1 + max(1, limit)
    everything = source.list_trending("VN", want)
    found = order_and_slice(everything, sort, start, limit)
    feed, kind = source.describe()
    by_url = {r["url"]: r["id"] for r in store.list_jobs()}

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
            like_count=c.like_count,
            share_count=c.share_count,
            position=c.position,
            media_url=c.media_url,
        )
        for c in found
    ]
    kept = [r for r in rows if not (r.seen or r.job_id)] if hide_seen else rows
    return ScanResult(
        rows=kept,
        feed_url=feed,
        kind=kind,
        hidden=len(rows) - len(kept),
        total=len(everything),
    )
