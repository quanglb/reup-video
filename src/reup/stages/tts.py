"""Stage 10 — sinh giọng đọc tiếng Việt cho từng câu đã dịch."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.media.audio import duration_ms
from reup.models import Segment, Transcript

logger = logging.getLogger(__name__)

# Đầu ra luôn là tiếng Việt: đây là pipeline lồng tiếng Việt, và Voice.json của
# CapCut cũng chỉ có giọng vi.
LANG = "vi"


def _hash_text(text: str) -> str:
    """Hash chỉ theo `text`, KHÔNG gồm `voice`.

    Cố ý bỏ `voice` ra khỏi key: an toàn không phải vì cache tự biết giọng
    có đổi hay không, mà vì đổi giọng ở chốt A luôn đi qua
    `set_voice` (`src/reup/web/service.py`), và hàm đó xoá sạch mọi
    `tts/seg_*.wav` cùng lúc đổi `job.overrides["voice"]`. Cache-skip ở
    `synthesize_all` chỉ dùng lại khi file `.wav` CÒN TỒN TẠI, nên đổi giọng
    luôn làm điều kiện đó trượt và ép tổng hợp lại — dù hash không chứa
    giọng. Nếu sau này có đường nào khác đổi `job.overrides["voice"]` mà
    không xoá wav như `set_voice` đang làm, cache này sẽ (sai) trả về audio
    giọng cũ — lúc đó phải sửa ở đây, trộn `voice` vào hash, không chỉ dựa
    vào việc dọn file bên ngoài.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_text_cache(job: Job) -> dict:
    path = job.tts_dir / "text_cache.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_prev_engines(job: Job) -> dict:
    """`engine` của lần chạy trước, theo `seg.id` — để câu dùng lại từ cache
    (không gọi lại adapter) vẫn giữ đúng nhãn capcut/edge_tts_fallback trong
    manifest mới, thay vì bị đoán nhầm thành "capcut" mặc định."""
    path = job.tts_dir / "manifest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        entry["id"]: entry.get("engine", "capcut")
        for entry in data.get("segments", [])
        if "id" in entry
    }


def synthesize_all(
    job: Job,
    transcript: Transcript,
    adapter,
    voice: str,
    concurrency: int = 1,
    batch_size: int = 8,
    notifier=None,
) -> list[dict]:
    """Sinh wav cho mọi đoạn, trả về bản kê. Dùng lại được từ stage fit.

    Trước khi gọi `adapter`, kiểm tra cache `tts/text_cache.json`
    (ghi bởi `write_manifest` lần chạy trước): nếu file wav của câu đã tồn tại
    VÀ hash của `text` hiện tại khớp hash lúc sinh, bỏ qua — không tốn một
    subprocess Python nào cho câu đó.

    Gộp các câu cần tổng hợp thành các batch `batch_size` câu để gọi
    `adapter.synthesize_batch` (giảm số lần gọi HTTP/spawn subprocess).
    Kết quả vẫn trả về đúng thứ tự `transcript.segments` gốc vì `fit`/`compose`
    dựa vào thứ tự này.
    """

    def reuse_cached(seg, out) -> dict:
        return {
            "id": seg.id,
            "path": out.name,
            "actual_ms": duration_ms(out),
            "engine": prev_engines.get(seg.id, "capcut"),
        }

    cache = _load_text_cache(job)
    prev_engines = _load_prev_engines(job)
    segments = transcript.segments
    results: list[dict | None] = [None] * len(segments)
    todo: list[tuple[int, Segment]] = []
    for i, seg in enumerate(segments):
        out = job.tts_segment(seg.id)
        if out.exists() and cache.get(str(seg.id)) == _hash_text(seg.text):
            results[i] = reuse_cached(seg, out)
        else:
            todo.append((i, seg))

    if not todo:
        return results

    batch_size = max(1, int(batch_size))

    # Chia todo thành các batch
    batches = [todo[k : k + batch_size] for k in range(0, len(todo), batch_size)]

    for batch in batches:
        items = [(seg.text, job.tts_segment(seg.id)) for _, seg in batch]
        if hasattr(adapter, "synthesize_batch"):
            batch_results = adapter.synthesize_batch(items, LANG, voice)
        else:
            batch_results = [
                adapter.synthesize(text, LANG, voice, out) for text, out in items
            ]
        for (i, seg), res in zip(batch, batch_results):
            out = job.tts_segment(seg.id)
            results[i] = {
                "id": seg.id,
                "path": out.name,
                "actual_ms": res.actual_ms,
                "engine": getattr(res, "engine", "capcut"),
            }

    # Báo cảnh báo Telegram nếu có câu fallback sang edge-tts
    fallback_ids = [
        r["id"] for r in results if r and r.get("engine") == "edge_tts_fallback"
    ]
    if fallback_ids and notifier is not None:
        try:
            if hasattr(notifier, "tts_fallback_warning"):
                notifier.tts_fallback_warning(job, fallback_ids)
        except Exception:
            logger.exception("Không gửi được cảnh báo TTS fallback qua notifier")

    return [r for r in results if r is not None]


def write_manifest(
    job: Job, voice: str, entries: list[dict], texts: dict[int, str]
) -> None:
    """Ghi `tts/manifest.json` và `tts/text_cache.json` cùng lúc.

    `texts` map `seg.id` → text đã dùng để tổng hợp (đọc lại được ở lần
    `redo` sau để biết câu nào đổi chữ, xem `synthesize_all`).
    """
    atomic_write(
        job.tts_dir / "manifest.json",
        json.dumps(
            {"voice": voice, "lang": LANG, "segments": entries},
            ensure_ascii=False,
            indent=2,
        ),
    )
    cache = {
        str(entry["id"]): _hash_text(texts[entry["id"]])
        for entry in entries
        if entry["id"] in texts
    }
    atomic_write(
        job.tts_dir / "text_cache.json",
        json.dumps(cache, ensure_ascii=False, indent=2),
    )


def run_with(job: Job, cfg: Config, adapter, notifier=None) -> None:
    # Phase 2: nguồn sự thật là bản dịch, không còn là transcript gốc.
    transcript = Transcript.load(job.translation_json)
    if not transcript.segments:
        raise ValueError(f"{job.translation_json} không có câu nào để đọc")

    # Giọng chọn ở chốt A thắng config: config.toml là của cả máy, còn lựa chọn
    # ở chốt A là của riêng job này (spec §8.2).
    voice = job.overrides.get("voice") or cfg.tts.voice

    # Đọc phiên âm từ pronunciation.json nếu có
    pron_file = job.tts_dir / "pronunciation.json"
    pronunciation_map = {}
    if pron_file.exists():
        try:
            pronunciation_map = json.loads(pron_file.read_text(encoding="utf-8"))
        except Exception:
            pronunciation_map = {}

    tts_segments = []
    texts = {}
    for seg in transcript.segments:
        tts_text = (
            pronunciation_map.get(str(seg.id))
            or pronunciation_map.get(seg.id)
            or seg.text
        )
        texts[seg.id] = tts_text
        tts_segments.append(
            Segment(
                id=seg.id,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                text=tts_text,
                text_source=seg.text_source,
                confidence=seg.confidence,
                flags=list(seg.flags),
            )
        )

    tts_transcript = Transcript(
        source_lang=transcript.source_lang,
        segments=tts_segments,
    )

    batch_size = getattr(cfg.tts, "batch_size", 8)
    entries = synthesize_all(
        job,
        tts_transcript,
        adapter,
        voice,
        concurrency=cfg.tts.concurrency,
        batch_size=batch_size,
        notifier=notifier,
    )
    write_manifest(job, voice, entries, texts)


def run(job: Job, cfg: Config) -> None:
    from reup.adapters.registry import make_tts
    from reup.notify import from_config

    run_with(job, cfg, make_tts(cfg), notifier=from_config(cfg))


SPEC = StageSpec(name="tts", produces=("tts/manifest.json",), run=run)
