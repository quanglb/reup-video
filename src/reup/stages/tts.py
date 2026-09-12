"""Stage 10 — sinh giọng đọc tiếng Việt cho từng câu đã dịch."""
from __future__ import annotations

import json

from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.models import Transcript

# Đầu ra luôn là tiếng Việt: đây là pipeline lồng tiếng Việt, và Voice.json của
# CapCut cũng chỉ có giọng vi.
LANG = "vi"


def synthesize_all(job: Job, transcript: Transcript, adapter, voice: str) -> list[dict]:
    """Sinh wav cho mọi đoạn, trả về bản kê. Dùng lại được từ stage fit."""
    entries = []
    for seg in transcript.segments:
        out = job.tts_segment(seg.id)
        result = adapter.synthesize(seg.text, LANG, voice, out)
        entries.append({"id": seg.id, "path": out.name, "actual_ms": result.actual_ms})
    return entries


def write_manifest(job: Job, voice: str, entries: list[dict]) -> None:
    atomic_write(
        job.tts_dir / "manifest.json",
        json.dumps(
            {"voice": voice, "lang": LANG, "segments": entries},
            ensure_ascii=False,
            indent=2,
        ),
    )


def run_with(job: Job, cfg: Config, adapter) -> None:
    # Phase 2: nguồn sự thật là bản dịch, không còn là transcript gốc.
    transcript = Transcript.load(job.translation_json)
    if not transcript.segments:
        raise ValueError(f"{job.translation_json} không có câu nào để đọc")

    # Giọng chọn ở chốt A thắng config: config.toml là của cả máy, còn lựa chọn
    # ở chốt A là của riêng job này (spec §8.2).
    voice = job.overrides.get("voice") or cfg.tts.voice
    write_manifest(job, voice, synthesize_all(job, transcript, adapter, voice))


def run(job: Job, cfg: Config) -> None:
    from reup.adapters.registry import make_tts

    run_with(job, cfg, make_tts(cfg))


SPEC = StageSpec(name="tts", produces=("tts/manifest.json",), run=run)
