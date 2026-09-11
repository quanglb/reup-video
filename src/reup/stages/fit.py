"""Stage 11 — khớp giọng đọc vào khe, rồi dựng thành một track lồng tiếng.

Spec §7.7. Chỉ có **hai** bậc xử lý khi câu dài quá khe:

    1. bắt LLM viết lại ngắn hơn (tối đa 2 lần)
    2. `atempo` nén tín hiệu, trần 1.25

Bản thiết kế đầu có ba bậc, với `rate` của CapCut đứng đầu. Đo thật thì `rate`
không đổi gì (1.5 chỉ ngắn 5.9%), và server còn cache theo text nên gọi lại cùng
một câu với rate khác trả về đúng file cũ. Cách duy nhất để đổi độ dài là **đổi
chữ** — nên viết lại lên bậc một.
"""
from __future__ import annotations

import json
from pathlib import Path

from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.ass import build_ass
from reup.core.stage import StageSpec
from reup.fit import decide_fit
from reup.media.audio import apply_tempo, build_timeline, duration_ms
from reup.models import Transcript
from reup.stages.translate import rewrite_shorter
from reup.stages.tts import LANG, write_manifest
from reup.text import count_syllables
from reup.translate import syllable_budget

MAX_REVISIONS = 2


def _fit_one(job: Job, seg, tts, llm, voice: str) -> dict:
    """Lặp viết lại cho tới khi vừa khe hoặc hết ngân sách. Trả một dòng báo cáo."""
    src = job.tts_segment(seg.id)
    actual = duration_ms(src)
    revision = 0

    while True:
        decision = decide_fit(actual, seg.slot_ms, revision, MAX_REVISIONS)
        if decision.action != "rewrite":
            break
        # Đổi chữ là đòn duy nhất thật sự rút ngắn được (xem docstring).
        seg.text = rewrite_shorter(llm, seg.text, syllable_budget(seg.slot_ms))
        revision += 1
        actual = tts.synthesize(seg.text, LANG, voice, src).actual_ms

    if decision.tempo > 1.0:
        fitted = src.with_name(f"{src.stem}_fitted.wav")
        apply_tempo(src, fitted, decision.tempo)
    else:
        fitted = src

    if decision.action == "overflow" and "overflow" not in seg.flags:
        seg.flags.append("overflow")

    return {
        "id": seg.id,
        "action": decision.action,
        "ratio": round(decision.ratio, 4),
        "tempo": round(decision.tempo, 4),
        "actual_ms": actual,
        "slot_ms": seg.slot_ms,
        "revision": revision,
        "fitted": fitted,
    }


def run_with(job: Job, cfg: Config, tts, llm) -> None:
    translation = Transcript.load(job.translation_json)
    if not translation.segments:
        raise ValueError(f"{job.translation_json} không có câu nào để khớp")

    voice = cfg.tts.voice
    placements: list[tuple[int, Path]] = []
    report: list[dict] = []
    manifest: list[dict] = []

    for seg in translation.segments:
        row = _fit_one(job, seg, tts, llm, voice)
        placements.append((seg.start_ms, row.pop("fitted")))
        report.append(row)
        manifest.append(
            {
                "id": seg.id,
                "path": job.tts_segment(seg.id).name,
                "actual_ms": row["actual_ms"],
            }
        )

    _save_translation(job, translation, report)
    write_manifest(job, voice, manifest)
    # Sinh phụ đề Ở ĐÂY chứ không ở stage translate: chữ chỉ chốt sau vòng viết
    # lại, sinh sớm thì sub hiện câu cũ còn giọng đọc câu mới.
    atomic_write(
        job.sub_ass,
        build_ass(
            translation,
            font=cfg.subtitle.font,
            size=cfg.subtitle.size,
            outline=cfg.subtitle.outline,
            position=cfg.subtitle.position,
        ),
    )
    atomic_write(
        job.tts_dir / "fit.json",
        json.dumps({"segments": report}, ensure_ascii=False, indent=2),
    )
    build_timeline(placements, total_ms=translation.total_ms, out=job.dub_wav)


def _save_translation(job: Job, translation: Transcript, report: list[dict]) -> None:
    """Ghi lại bản dịch kèm chữ đã viết lại, để chốt A thấy đúng thứ đã đọc."""
    revisions = {r["id"]: r["revision"] for r in report}
    segments = []
    for seg in translation.segments:
        budget = syllable_budget(seg.slot_ms)
        syllables = count_syllables(seg.text, LANG)
        # Tính lại `over_budget` chứ không chép cờ cũ: một câu đã viết lại cho
        # vừa mà vẫn đỏ ở chốt A là bắt người duyệt sửa thứ đã xong rồi.
        flags = [f for f in seg.flags if f != "over_budget"]
        if syllables > budget:
            flags.append("over_budget")
        segments.append(
            {
                "id": seg.id,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "slot_ms": seg.slot_ms,
                "syllable_budget": budget,
                "text": seg.text,
                "syllables": syllables,
                "text_source": seg.text_source,
                "confidence": seg.confidence,
                "revision": revisions.get(seg.id, 0),
                "flags": flags,
            }
        )
    atomic_write(
        job.translation_json,
        json.dumps(
            {"source_lang": LANG, "target_lang": LANG, "segments": segments},
            ensure_ascii=False,
            indent=2,
        ),
    )


def run(job: Job, cfg: Config) -> None:
    from reup.adapters.registry import make_llm, make_tts

    run_with(job, cfg, make_tts(cfg), make_llm(cfg))


SPEC = StageSpec(name="fit", produces=("dub.wav", "sub.ass"), run=run)
