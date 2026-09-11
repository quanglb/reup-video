"""Stage 10 — sinh giọng đọc cho từng câu."""
from __future__ import annotations

import json

from reup.adapters.stub_tts import StubTTS
from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.models import Transcript

# Phase 2 thay bằng adapter CapCut thật và cho chọn giọng ở chốt A.
DEFAULT_VOICES = {"vi": "stub-vi-1", "zh": "stub-zh-1", "en": "stub-en-1"}


def voice_for(lang: str) -> str:
    return DEFAULT_VOICES.get(lang, "stub-vi-1")


def run(job: Job, cfg: Config) -> None:
    # Phase 1 đọc transcript gốc vì chưa có stage translate.
    # Phase 2 đổi sang job.translation_json.
    transcript = Transcript.load(job.transcript_json)
    if not transcript.segments:
        raise ValueError(f"{job.transcript_json} không có câu nào để đọc")

    adapter = StubTTS()
    # Phase 1 đọc chính văn bản gốc, nên ngôn ngữ đếm âm tiết là ngôn ngữ nguồn.
    # Phase 2 đọc translation.json và chuyển sang "vi".
    lang = transcript.source_lang
    voice = voice_for(lang)
    entries = []
    for seg in transcript.segments:
        out = job.tts_segment(seg.id)
        result = adapter.synthesize(seg.text, lang, voice, out)
        entries.append(
            {"id": seg.id, "path": out.name, "actual_ms": result.actual_ms}
        )

    atomic_write(
        job.tts_dir / "manifest.json",
        json.dumps(
            {"voice": voice, "lang": lang, "segments": entries},
            ensure_ascii=False,
            indent=2,
        ),
    )


SPEC = StageSpec(name="tts", produces=("tts/manifest.json",), run=run)
