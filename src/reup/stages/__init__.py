"""Thứ tự stage. Runner chạy theo đúng danh sách này."""
from __future__ import annotations

from reup.config import Config
from reup.core.stage import StageSpec
from reup.stages import (
    asr,
    compose,
    demux,
    fetch,
    fit,
    ocr,
    reconcile,
    separate,
    subdetect,
    translate,
    tts,
)

# Mười một trong mười ba stage của spec. Còn thiếu: discover(1), export(13).
#
# subdetect và ocr không phụ thuộc nhánh audio (demux/separate/asr) nên về lý
# thuyết chạy song song được; runner hiện chạy tuần tự nên xếp sau cho dễ đọc.
ALL_STAGES: list[StageSpec] = [
    fetch.SPEC,
    demux.SPEC,
    separate.SPEC,
    asr.SPEC,
    subdetect.SPEC,
    ocr.SPEC,
    reconcile.SPEC,
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
    stages = list(ALL_STAGES)
    if cfg.audio.mode == "drop_original":
        stages = [s for s in stages if s.name != "separate"]
    return stages
