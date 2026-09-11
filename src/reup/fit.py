"""Luật khớp giọng đọc vào khe thời gian — spec §7.7.

Hàm thuần, tách khỏi stage vì đây là chỗ dễ sai nhất trong pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass

PAD_BELOW = 0.85
TEMPO_CEILING = 1.15
REWRITE_CEILING = 1.5
CAPPED_TEMPO = 1.25


@dataclass(frozen=True)
class FitDecision:
    action: str
    ratio: float
    tempo: float


def decide_fit(
    actual_ms: int, slot_ms: int, revision: int, max_revisions: int = 2
) -> FitDecision:
    if slot_ms <= 0:
        raise ValueError(f"slot_ms phải dương, nhận {slot_ms}")

    ratio = actual_ms / slot_ms

    if ratio < PAD_BELOW:
        return FitDecision("pad", ratio, 1.0)
    if ratio <= TEMPO_CEILING:
        # Chỉ nén, không bao giờ kéo chậm: giọng chậm nghe lè nhè.
        return FitDecision("tempo", ratio, max(ratio, 1.0))
    if revision < max_revisions:
        return FitDecision("rewrite", ratio, 1.0)
    if ratio <= REWRITE_CEILING:
        return FitDecision("tempo_capped", ratio, CAPPED_TEMPO)
    return FitDecision("overflow", ratio, CAPPED_TEMPO)
