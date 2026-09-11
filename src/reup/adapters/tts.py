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


class TTSAdapter(Protocol):
    def voices(self, lang: str) -> list[Voice]: ...

    def synthesize(
        self, text: str, lang: str, voice: str, out: Path
    ) -> TTSResult: ...
