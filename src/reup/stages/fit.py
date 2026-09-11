"""Stage 11 — nén hoặc đệm từng câu cho khớp khe, rồi dựng thành một track lồng tiếng."""
from __future__ import annotations

import json

from pathlib import Path

from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.fit import decide_fit
from reup.media.audio import apply_tempo, build_timeline, duration_ms
from reup.models import Transcript

MAX_REVISIONS = 2


def run(job: Job, cfg: Config) -> None:
    transcript = Transcript.load(job.transcript_json)
    placements: list[tuple[int, Path]] = []
    report: list[dict] = []

    for seg in transcript.segments:
        src = job.tts_segment(seg.id)
        actual = duration_ms(src)

        # Phase 1 không có stage translate nên ngân sách viết lại đã cạn sẵn:
        # nhánh "rewrite" không bao giờ chạy. Phase 2 nối vòng lặp thật.
        decision = decide_fit(actual, seg.slot_ms, MAX_REVISIONS, MAX_REVISIONS)

        if decision.tempo > 1.0:
            fitted = src.with_name(f"{src.stem}_fitted.wav")
            apply_tempo(src, fitted, decision.tempo)
        else:
            fitted = src

        if decision.action == "overflow" and "overflow" not in seg.flags:
            seg.flags.append("overflow")

        placements.append((seg.start_ms, fitted))
        report.append({
            "id": seg.id,
            "action": decision.action,
            "ratio": round(decision.ratio, 4),
            "tempo": round(decision.tempo, 4),
            "actual_ms": actual,
            "slot_ms": seg.slot_ms,
        })

    transcript.save(job.transcript_json)
    atomic_write(
        job.tts_dir / "fit.json",
        json.dumps({"segments": report}, ensure_ascii=False, indent=2),
    )
    build_timeline(placements, total_ms=transcript.total_ms, out=job.dub_wav)


SPEC = StageSpec(name="fit", produces=("dub.wav",), run=run)
