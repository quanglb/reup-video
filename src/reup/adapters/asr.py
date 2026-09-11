"""Biên giới ra dịch vụ nhận dạng tiếng nói.

Có hai implement nên interface mới có ý nghĩa: `WhisperASR` chạy local trên
Neural Engine, `CapCutSTT` đẩy lên mạng nhưng không tốn CPU của máy. Đổi bằng
`asr.engine` trong config — hữu ích khi máy Air quá ì (spec R5).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from reup.models import Segment


class ASRAdapter(Protocol):
    name: str

    def transcribe(
        self, audio: Path, lang: str | None
    ) -> tuple[str, list[Segment]]: ...
