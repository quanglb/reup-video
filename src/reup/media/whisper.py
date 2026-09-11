"""Bọc mlx-whisper. Hàm thuần — nhận đường dẫn audio, trả câu kèm mốc thời gian."""
from __future__ import annotations

from pathlib import Path

from reup.models import Segment


def transcribe(
    audio: Path, model: str, language: str | None
) -> tuple[str, list[Segment]]:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=model,
        language=language,
        word_timestamps=True,
    )
    segments = [
        Segment(
            id=idx,
            start_ms=round(float(raw["start"]) * 1000),
            end_ms=round(float(raw["end"]) * 1000),
            text=raw["text"].strip(),
            text_source="asr",
            confidence=1.0,
        )
        for idx, raw in enumerate(result.get("segments", []), start=1)
        if raw["text"].strip()
    ]
    return result.get("language", language or "unknown"), segments
