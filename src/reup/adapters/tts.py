"""Biên giới ra dịch vụ tổng hợp giọng nói.

Source CapCut TTS cắm vào đây ở phase 2: viết một class implement
TTSAdapter, không sửa gì trong pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    lang: str


@dataclass(frozen=True)
class TTSResult:
    path: Path
    actual_ms: int
    # "capcut" (thật) hay "edge_tts_fallback" (âm thầm đổi giọng khi CapCut
    # lỗi — xem capcut_driver.py). Mặc định "capcut" để không phải sửa mọi
    # chỗ dựng TTSResult không liên quan (StubTTS, đường im lặng câu rỗng...).
    engine: str = "capcut"


class TTSAdapter(Protocol):
    def voices(self, lang: str) -> list[Voice]: ...

    def synthesize(
        self, text: str, lang: str, voice: str, out: Path
    ) -> TTSResult: ...
