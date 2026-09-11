"""Thứ tự stage. Runner chạy theo đúng danh sách này."""
from __future__ import annotations

from reup.core.stage import StageSpec
from reup.stages import asr, compose, demux, fetch, fit, tts

# Phase 1: sáu trong mười ba stage của spec.
# Còn thiếu: discover(1), separate(4), subdetect(6), ocr(7),
#            reconcile(8), translate(9), export(13).
PHASE1_STAGES: list[StageSpec] = [
    fetch.SPEC,
    demux.SPEC,
    asr.SPEC,
    tts.SPEC,
    fit.SPEC,
    compose.SPEC,
]
