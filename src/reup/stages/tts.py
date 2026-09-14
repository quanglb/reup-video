"""Stage 10 — sinh giọng đọc tiếng Việt cho từng câu đã dịch."""
from __future__ import annotations

import hashlib
import json

from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.models import Transcript

# Đầu ra luôn là tiếng Việt: đây là pipeline lồng tiếng Việt, và Voice.json của
# CapCut cũng chỉ có giọng vi.
LANG = "vi"


from reup.media.audio import duration_ms


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
) -> list[dict]:
    """Sinh wav cho mọi đoạn, trả về bản kê. Dùng lại được từ stage fit.

    Trước khi gọi `adapter.synthesize`, kiểm tra cache `tts/text_cache.json`
    (ghi bởi `write_manifest` lần chạy trước): nếu file wav của câu đã tồn tại
    VÀ hash của `text` hiện tại khớp hash lúc sinh, bỏ qua — không tốn một
    subprocess Python nào cho câu đó. Việc này diễn ra TRƯỚC khi đưa câu vào
    `ThreadPoolExecutor`, để các câu đã cache không hề chạm tới pool: chỉ
    những câu thật sự cần tổng hợp mới được chạy song song.

    Mỗi câu cần tổng hợp là một subprocess độc lập với server CapCut, không
    phụ thuộc câu trước, nên chạy song song tối đa `concurrency` câu cùng lúc
    (cùng cách `run_jobs` ở core/runner.py chạy nhiều job song song). Kết quả
    vẫn trả về đúng thứ tự `transcript.segments` gốc — không phải thứ tự hoàn
    thành — vì `fit`/`compose` dựa vào thứ tự này.
    """

    def synthesize(seg) -> dict:
        out = job.tts_segment(seg.id)
        result = adapter.synthesize(seg.text, LANG, voice, out)
        return {
            "id": seg.id,
            "path": out.name,
            "actual_ms": result.actual_ms,
            "engine": getattr(result, "engine", "capcut"),
        }

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
    todo: list[tuple[int, object]] = []
    for i, seg in enumerate(segments):
        out = job.tts_segment(seg.id)
        if out.exists() and cache.get(str(seg.id)) == _hash_text(seg.text):
            results[i] = reuse_cached(seg, out)
        else:
            todo.append((i, seg))

    if not todo:
        return results

    concurrency = max(1, int(concurrency))
    if concurrency == 1 or len(todo) <= 1:
        for i, seg in todo:
            results[i] = synthesize(seg)
        return results

    from concurrent.futures import ThreadPoolExecutor, as_completed

    # KHÔNG dùng `pool.map`: nó nộp hết việc lên ngay từ đầu, nên một câu lỗi
    # (vd. CapCutError khi allow_edge_fallback=false) không chặn được các câu
    # ĐÃ nộp khác — chúng vẫn chạy hết 3 lần retry với backoff của riêng mình
    # trước khi exception từ iterator của `map` mới lộ ra. Trên job 50 câu đó
    # là ~50×3 request CapCut vô ích thay vì dừng sau ~3, ngược hẳn mục đích
    # nghỉ nhịp ở `_count_success_and_maybe_pause` (né ngưỡng gãy ~15 request
    # liên tiếp của CapCut). Dùng `submit` + `as_completed` để vừa nghe lỗi
    # sớm, vừa huỷ được các future CHƯA kịp chạy khi lỗi xảy ra.
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(synthesize, seg): i for i, seg in todo}
        try:
            for future in as_completed(futures):
                i = futures[future]
                results[i] = future.result()
        except BaseException:
            for other in futures:
                other.cancel()
            pool.shutdown(cancel_futures=True)
            raise
    return results


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


def run_with(job: Job, cfg: Config, adapter) -> None:
    # Phase 2: nguồn sự thật là bản dịch, không còn là transcript gốc.
    transcript = Transcript.load(job.translation_json)
    if not transcript.segments:
        raise ValueError(f"{job.translation_json} không có câu nào để đọc")

    # Giọng chọn ở chốt A thắng config: config.toml là của cả máy, còn lựa chọn
    # ở chốt A là của riêng job này (spec §8.2).
    voice = job.overrides.get("voice") or cfg.tts.voice
    entries = synthesize_all(job, transcript, adapter, voice, cfg.tts.concurrency)
    texts = {seg.id: seg.text for seg in transcript.segments}
    write_manifest(job, voice, entries, texts)


def run(job: Job, cfg: Config) -> None:
    from reup.adapters.registry import make_tts

    run_with(job, cfg, make_tts(cfg))


SPEC = StageSpec(name="tts", produces=("tts/manifest.json",), run=run)
