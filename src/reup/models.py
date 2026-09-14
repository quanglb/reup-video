"""Cấu trúc dữ liệu đi qua pipeline, kèm đọc ghi JSON."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
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
        """Bỏ qua trường lạ trong mỗi đoạn.

        `translation.json` là superset của `transcript.json` — nó mang thêm
        slot_ms, syllable_budget, syllables, revision. Nhờ luật này mà cả hai
        file dùng chung một bộ đọc.
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(Segment)}
        return cls(
            source_lang=raw["source_lang"],
            segments=[
                Segment(**{k: v for k, v in s.items() if k in known})
                for s in raw["segments"]
            ],
        )
