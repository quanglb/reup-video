"""Stage 5 — nghe audio, viết ra câu kèm mốc thời gian."""
from __future__ import annotations

from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.whisper import transcribe
from reup.models import Transcript


def pick_audio(job: Job):
    """Nghe giọng đã tách nếu có — nhạc nền to là nguyên nhân chính làm ASR sai.

    `audio.mode = "drop_original"` bỏ stage separate nên không có vocals.wav;
    khi đó nghe thẳng bản trộn.
    """
    return job.vocals if job.vocals.exists() else job.full_16k


def run(job: Job, cfg: Config) -> None:
    language = None if job.source_lang == "auto" else job.source_lang
    detected_lang, segments = transcribe(
        pick_audio(job), cfg.profile.whisper_model, language
    )
    if not segments:
        raise ValueError(
            f"ASR không nhận được câu nào từ {job.full_16k} — "
            "video có thể không có tiếng nói"
        )

    transcript = Transcript(source_lang=detected_lang, segments=segments)
    transcript.save(job.asr_json)
    # Phase 1 chưa có stage reconcile, nên asr là nguồn sự thật luôn.
    # Phase 3 bỏ dòng dưới khi reconcile xuất hiện.
    transcript.save(job.transcript_json)


SPEC = StageSpec(name="asr", produces=("asr.json", "transcript.json"), run=run)
