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
    frame,
    meta,
    ocr,
    policy,
    precheck,
    pronounce,
    reconcile,
    separate,
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


# Dự án có ảnh khung (vd. LOL): chỉ tải về, đặt vào khung rồi xuất — không
# dịch, không lồng tiếng.
FRAME_STAGES: list[StageSpec] = [
    fetch.SPEC, precheck.SPEC, meta.SPEC, frame.SPEC, policy.SPEC, export.SPEC,
]


def stages_for(cfg: Config, project: str | None = None) -> list[StageSpec]:
    """Chuỗi stage cho một cấu hình (và dự án của job, nếu có).

    `audio.mode = "drop_original"` bỏ hẳn `separate` — mất nhạc nền nhưng cắt
    được stage nặng nhất. Đây là nút thoát hiểm khi máy quá ì (spec §9).
    """
    from reup import projects

    if projects.get(project) is not None:
        return list(FRAME_STAGES)
    stages = list(ALL_STAGES)
    if cfg.audio.mode == "drop_original":
        stages = [s for s in stages if s.name != "separate"]
    return stages


def stages_for_job(cfg: Config, job) -> list[StageSpec]:
    return stages_for(cfg, job.settings.get("project"))
