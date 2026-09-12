"""Stage 5 — nghe audio, viết ra câu kèm mốc thời gian."""
from __future__ import annotations

from reup.adapters.registry import make_asr
from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.models import Transcript


def pick_audio(job: Job):
    """Nghe giọng đã tách nếu có — nhạc nền to là nguyên nhân chính làm ASR sai.

    `audio.mode = "drop_original"` bỏ stage separate nên không có vocals.wav;
    khi đó nghe thẳng bản trộn.
    """
    return job.vocals if job.vocals.exists() else job.full_16k


def run(job: Job, cfg: Config) -> None:
    language = None if job.source_lang == "auto" else job.source_lang
    # Engine chọn ở registry theo `asr.engine`: Whisper local, hoặc CapCut STT
    # khi máy quá ì (spec R5).
    detected_lang, segments = make_asr(cfg).transcribe(pick_audio(job), language)
    if not segments:
        raise ValueError(
            f"ASR không nhận được câu nào từ {job.full_16k} — "
            "video có thể không có tiếng nói"
        )

    Transcript(source_lang=detected_lang, segments=segments).save(job.asr_json)
    # transcript.json thuộc về stage reconcile (phase 3) — nó mới là nguồn sự thật.


SPEC = StageSpec(name="asr", produces=("asr.json",), run=run)
