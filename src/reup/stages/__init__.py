"""Thứ tự stage. Runner chạy theo đúng danh sách này."""
from __future__ import annotations

from reup.config import Config
from reup.core.stage import StageSpec
from reup.stages import (
    asr,
    compose,
    demux,
    export,
    fetch,
    fit,
    meta,
    ocr,
    policy,
    precheck,
    pronounce,
    reconcile,
    separate,
    split,
    subdetect,
    translate,
    tts,
)

# Mười sáu stage của pipeline.
#
# precheck (chỉ luật, 0 token) chặn sớm nguồn vi phạm trước khi tốn công.
# meta sinh tiêu đề + dịch chữ cố định TRƯỚC compose để in được lên hình.
# policy (luật + Apple Vision + một lượt AI) duyệt thành phẩm trước khi xuất.
#
# subdetect và ocr không phụ thuộc nhánh audio (demux/separate/asr) nên về lý
# thuyết chạy song song được; runner hiện chạy tuần tự nên xếp sau cho dễ đọc.
ALL_STAGES: list[StageSpec] = [
    fetch.SPEC,
    precheck.SPEC,
    demux.SPEC,
    separate.SPEC,
    asr.SPEC,
    subdetect.SPEC,
    ocr.SPEC,
    reconcile.SPEC,
    translate.SPEC,
    pronounce.SPEC,
    tts.SPEC,
    fit.SPEC,
    meta.SPEC,
    compose.SPEC,
    policy.SPEC,
    export.SPEC,
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


# Job cha của video dài được yêu cầu tách: chỉ tải, nghe (bỏ Demucs — nghe
# tiếng gốc là đủ để AI hiểu nội dung) rồi cắt thành các job con.
SPLIT_STAGES: list[StageSpec] = [
    fetch.SPEC, precheck.SPEC, demux.SPEC, asr.SPEC, split.SPEC,
]


def stages_for_job(cfg: Config, job) -> list[StageSpec]:
    settings = job.settings
    if settings.get("split"):
        done = split.plan(job)
        # split.json có mà không có phần nào = video không đủ dài để tách → chạy
        # như video thường.
        if done is None or done.get("parts"):
            return list(SPLIT_STAGES)
    return stages_for(cfg)
