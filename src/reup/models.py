"""Cấu trúc dữ liệu đi qua pipeline, kèm đọc ghi JSON."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Segment:
    id: int
    start_ms: int
    end_ms: int
    text: str
    text_source: str = "asr"
    confidence: float = 1.0
    flags: list[str] = field(default_factory=list)

    @property
    def slot_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class Transcript:
    source_lang: str
    segments: list[Segment]

    @property
    def total_ms(self) -> int:
        return max((s.end_ms for s in self.segments), default=0)

    def save(self, path: Path) -> None:
        payload = {
            "source_lang": self.source_lang,
            "segments": [asdict(s) for s in self.segments],
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> Transcript:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            source_lang=raw["source_lang"],
            segments=[Segment(**s) for s in raw["segments"]],
        )
