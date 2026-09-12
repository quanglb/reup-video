"""Stage 8 — hợp nhất ASR và OCR thành `transcript.json`, nguồn sự thật.

LLM chỉ được gọi cho những đoạn hai bên lệch nhau, và chỉ được **chọn một
trong hai** chứ không được viết mới (spec §7.5, rủi ro R2).
"""
from __future__ import annotations

import json

from reup.adapters.llm import LLMAdapter
from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.models import Segment, Transcript
from reup.reconcile import best_ocr_line, decide

CHOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "choices": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "pick": {"type": "string", "enum": ["asr", "ocr"]},
                },
                "required": ["id", "pick"],
            },
        }
    },
    "required": ["choices"],
}


def build_prompt(cases: list[dict]) -> str:
    blocks = []
    for c in cases:
        blocks.append(
            f"### Đoạn {c['id']}\n"
            f"- A (nghe từ audio): {c['asr']}\n"
            f"- B (đọc từ phụ đề cháy trên hình): {c['ocr']}"
        )
    return (
        "Hai nguồn văn bản của cùng một video không khớp nhau ở các đoạn dưới đây.\n"
        "A do máy nghe từ audio, hay sai khi nhạc to hoặc nói nhanh.\n"
        "B do máy đọc chữ cháy sẵn trên hình, hay sai khi chữ bị che hoặc nền rối.\n\n"
        "Với mỗi đoạn, chọn bản nào đúng hơn.\n\n"
        "TUYỆT ĐỐI KHÔNG viết câu mới, không sửa chữ, không trộn hai bản. "
        'Chỉ trả về "asr" hoặc "ocr".\n\n'
        'Trả JSON: {"choices": [{"id": <số>, "pick": "asr"|"ocr"}]}\n\n'
        + "\n\n".join(blocks)
    )


def run_with(job: Job, cfg: Config, llm: LLMAdapter) -> None:
    asr = Transcript.load(job.asr_json)
    if not asr.segments:
        raise ValueError(f"{job.asr_json} không có câu nào")

    lines = json.loads(job.ocr_json.read_text(encoding="utf-8"))["lines"]

    resolved: dict[int, tuple[str, str, list[str]]] = {}
    pending: list[dict] = []
    for seg in asr.segments:
        line = best_ocr_line(seg, lines)
        text, source, flags = decide(seg, line)
        if source == "llm":
            pending.append(
                {"id": seg.id, "asr": seg.text, "ocr": line["text"], "flags": flags}
            )
        else:
            resolved[seg.id] = (text, source, flags)

    picks: dict[int, str] = {}
    if pending:
        answer = llm.complete_json(build_prompt(pending), CHOICE_SCHEMA)
        picks = {int(c["id"]): c["pick"] for c in answer.get("choices", [])}

    segments = []
    for seg in asr.segments:
        if seg.id in resolved:
            text, source, flags = resolved[seg.id]
        else:
            case = next(c for c in pending if c["id"] == seg.id)
            # Thiếu lựa chọn thì giữ ASR: đó là bản có mốc thời gian đáng tin.
            pick = picks.get(seg.id, "asr")
            text = case["ocr"] if pick == "ocr" else case["asr"]
            source, flags = pick, case["flags"]
        segments.append(
            Segment(
                id=seg.id,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                text=text,
                text_source=source,
                confidence=seg.confidence,
                flags=list(flags),
            )
        )

    Transcript(source_lang=asr.source_lang, segments=segments).save(job.transcript_json)


def run(job: Job, cfg: Config) -> None:
    from reup.adapters.registry import make_llm

    run_with(job, cfg, make_llm(cfg, "reconcile"))


SPEC = StageSpec(name="reconcile", produces=("transcript.json",), run=run)
