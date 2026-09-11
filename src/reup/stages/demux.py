"""Stage 3 — tách audio ra hai bản: 16kHz mono cho ASR, 48kHz stereo để mix."""
from __future__ import annotations

from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.audio import extract_audio


def run(job: Job, cfg: Config) -> None:
    extract_audio(job.source_video, job.full_16k, sample_rate=16000, channels=1)
    extract_audio(job.source_video, job.full_48k, sample_rate=48000, channels=2)


SPEC = StageSpec(
    name="demux",
    produces=("audio/full_16k.wav", "audio/full_48k.wav"),
    run=run,
)
