"""Thứ tự stage. Runner chạy theo đúng danh sách này."""
from __future__ import annotations

from reup.core.stage import StageSpec
from reup.stages import asr, compose, demux, fetch, fit, translate, tts

# Phase 2: bảy trong mười ba stage của spec.
# Còn thiếu: discover(1), separate(4), subdetect(6), ocr(7), reconcile(8), export(13).
PHASE2_STAGES: list[StageSpec] = [
    fetch.SPEC,
    demux.SPEC,
    asr.SPEC,
    translate.SPEC,
    tts.SPEC,
    fit.SPEC,
    compose.SPEC,
]

# Tên cũ, giữ để không gãy chỗ nào còn import.
PHASE1_STAGES = PHASE2_STAGES
