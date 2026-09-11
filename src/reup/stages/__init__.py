"""Thứ tự stage. Runner chạy theo đúng danh sách này."""
from __future__ import annotations

from reup.config import Config
from reup.core.stage import StageSpec
from reup.stages import asr, compose, demux, fetch, fit, separate, translate, tts

# Tám trong mười ba stage của spec.
# Còn thiếu: discover(1), subdetect(6), ocr(7), reconcile(8), export(13).
ALL_STAGES: list[StageSpec] = [
    fetch.SPEC,
    demux.SPEC,
    separate.SPEC,
    asr.SPEC,
    translate.SPEC,
    tts.SPEC,
    fit.SPEC,
    compose.SPEC,
]


def stages_for(cfg: Config) -> list[StageSpec]:
    """Chuỗi stage cho một cấu hình.

    `audio.mode = "drop_original"` bỏ hẳn `separate` — mất nhạc nền nhưng cắt
    được stage nặng nhất. Đây là nút thoát hiểm khi máy quá ì (spec §9).
    """
    if cfg.audio.mode == "drop_original":
        return [s for s in ALL_STAGES if s.name != "separate"]
    return list(ALL_STAGES)
