"""Stage 4 — tách giọng người khỏi nhạc nền và tiếng động.

Hai đầu ra phục vụ hai việc khác nhau, nên có định dạng khác nhau:

- `vocals.wav` — 16kHz mono, chỉ để stage `asr` nghe. Whisper tự resample về
  16k nên giữ 44.1k ở đây chỉ tốn chỗ.
- `bgm.wav` — 48kHz stereo, để `compose` trộn với giọng lồng. Đây là thứ người
  xem nghe thấy nên phải giữ chất lượng.

Đầu vào là `full_48k.wav` chứ không phải `full_16k.wav` như bản thiết kế đầu:
Demucs chạy ở 44.1kHz stereo, đưa nó bản 16k mono thì vừa tách kém vừa cho ra
nhạc nền 16k mono không dùng được cho khâu trộn.
"""
from __future__ import annotations

from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.audio import to_wav
from reup.media.demucs import separate


def run(job: Job, cfg: Config) -> None:
    work = job.audio_dir / "demucs"
    vocals, bgm = separate(
        job.full_48k,
        work,
        model=cfg.profile.demucs_model,
        segment=cfg.profile.demucs_segment,
        device=cfg.profile.demucs_device,
    )
    to_wav(vocals, job.vocals, sample_rate=16000, channels=1)
    to_wav(bgm, job.bgm, sample_rate=48000, channels=2)


SPEC = StageSpec(
    name="separate", produces=("audio/vocals.wav", "audio/bgm.wav"), run=run
)
