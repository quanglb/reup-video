"""ASR local bằng mlx-whisper. Chỉ bọc `media/whisper.py` cho vừa interface."""
from __future__ import annotations

from pathlib import Path

from reup.media.whisper import transcribe
from reup.models import Segment


class WhisperASR:
    name = "whisper"

    def __init__(self, model: str) -> None:
        self.model = model

    def transcribe(self, audio: Path, lang: str | None) -> tuple[str, list[Segment]]:
        return transcribe(audio, self.model, lang)
