"""Stage 9 — dịch sang tiếng Việt trong ngân sách âm tiết của từng khe.

Spec §7.6. Hai luật cứng:
- Prompt yêu cầu dịch **tự nhiên trong trần**, không phải dịch xong rồi cắt.
- Đoạn vượt trần vẫn được giữ nguyên chữ và gắn cờ `over_budget`. Tự cắt ở đây
  là giấu vấn đề khỏi chốt A, nơi người thật nhìn thấy và sửa được.
"""
from __future__ import annotations

import json

from reup.adapters.llm import LLMAdapter
from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.models import Transcript
from reup.text import count_syllables
from reup.translate import syllable_budget

TARGET_LANG = "vi"

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["id", "text"],
            },
        }
    },
    "required": ["segments"],
}

_LANG_NAMES = {"zh": "tiếng Trung", "en": "tiếng Anh", "auto": "ngôn ngữ gốc"}


def build_prompt(transcript: Transcript, budgets: dict[int, int]) -> str:
    """Một lượt gọi cho cả video, mỗi đoạn kèm câu trước và câu sau.

    Gọi từng đoạn một sẽ mất ngữ cảnh: đại từ và văn phong nhảy loạn giữa các
    câu. Gọi cả lượt cũng rẻ hơn nhiều lần gọi lẻ.
    """
    src = _LANG_NAMES.get(transcript.source_lang, transcript.source_lang)
    lines = []
    segs = transcript.segments
    for i, seg in enumerate(segs):
        before = segs[i - 1].text if i > 0 else "(đầu video)"
        after = segs[i + 1].text if i + 1 < len(segs) else "(cuối video)"
        lines.append(
            f"### Đoạn {seg.id}\n"
            f"- Câu trước: {before}\n"
            f"- **Cần dịch: {seg.text}**\n"
            f"- Câu sau: {after}\n"
            f"- Trần: tối đa {budgets[seg.id]} âm tiết"
        )

    return (
        f"Bạn đang lồng tiếng Việt cho một video ngắn {src}.\n\n"
        "Dịch từng đoạn sang tiếng Việt tự nhiên, đúng văn nói, như người thật "
        "đang kể chứ không phải đọc văn bản dịch.\n\n"
        "RÀNG BUỘC QUAN TRỌNG — mỗi đoạn có một trần âm tiết, vì giọng đọc phải "
        "vừa đúng khe thời gian của câu gốc:\n"
        "- Viết câu ngắn gọn để **nằm trong trần ngay từ đầu**. Đừng dịch dài rồi cắt cụt.\n"
        "- Tiếng Việt tính âm tiết theo tiếng, mỗi tiếng cách nhau bằng khoảng trắng. "
        '"thịt kho tàu" là 3 âm tiết.\n'
        "- Thà bỏ chi tiết phụ còn hơn vượt trần.\n"
        "- Giữ đại từ và văn phong nhất quán giữa các đoạn — dùng câu trước và "
        "câu sau làm ngữ cảnh, nhưng **chỉ dịch phần in đậm**.\n\n"
        "Trả về JSON: {\"segments\": [{\"id\": <số>, \"text\": \"<bản dịch>\"}]}. "
        "Phải có đủ mọi id, không thêm id nào lạ.\n\n"
        + "\n\n".join(lines)
    )


def run_with(job: Job, cfg: Config, llm: LLMAdapter) -> None:
    transcript = Transcript.load(job.transcript_json)
    if not transcript.segments:
        raise ValueError(f"{job.transcript_json} không có câu nào để dịch")

    budgets = {s.id: syllable_budget(s.slot_ms) for s in transcript.segments}
    answer = llm.complete_json(build_prompt(transcript, budgets), RESPONSE_SCHEMA)

    by_id = {int(item["id"]): item["text"].strip() for item in answer.get("segments", [])}
    missing = [s.id for s in transcript.segments if s.id not in by_id]
    if missing:
        raise ValueError(
            f"bản dịch thiếu đoạn {missing} — mất câu còn tệ hơn dịch sai, không chạy tiếp"
        )

    segments = []
    for seg in transcript.segments:
        text = by_id[seg.id]
        budget = budgets[seg.id]
        syllables = count_syllables(text, TARGET_LANG)
        segments.append(
            {
                "id": seg.id,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "slot_ms": seg.slot_ms,
                "syllable_budget": budget,
                "text": text,
                "syllables": syllables,
                "text_source": "llm",
                "confidence": 1.0,
                "revision": 0,
                "flags": ["over_budget"] if syllables > budget else [],
            }
        )

    atomic_write(
        job.translation_json,
        json.dumps(
            {"source_lang": TARGET_LANG, "target_lang": TARGET_LANG, "segments": segments},
            ensure_ascii=False,
            indent=2,
        ),
    )


def run(job: Job, cfg: Config) -> None:
    from reup.adapters.registry import make_llm

    run_with(job, cfg, make_llm(cfg))


SPEC = StageSpec(name="translate", produces=("translation.json",), run=run)


REWRITE_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def build_rewrite_prompt(text: str, budget: int, syllables: int) -> str:
    return (
        "Câu tiếng Việt dưới đây dài quá khe thời gian của nó khi đọc lên.\n\n"
        f"Câu hiện tại ({syllables} âm tiết): {text}\n"
        f"Trần cho phép: {budget} âm tiết\n\n"
        "Viết lại ngắn hơn, giữ đúng ý chính, vẫn là văn nói tự nhiên.\n"
        "Bỏ được chi tiết phụ thì bỏ. Đừng cắt cụt giữa câu.\n"
        "Tiếng Việt tính âm tiết theo tiếng, cách nhau bằng khoảng trắng.\n\n"
        'Trả về JSON: {"text": "<câu đã viết lại>"}'
    )


def rewrite_shorter(llm: LLMAdapter, text: str, budget: int) -> str:
    """Bảo LLM viết lại một câu cho vừa trần. Không đảm bảo nó vừa — fit đo lại."""
    syllables = count_syllables(text, TARGET_LANG)
    answer = llm.complete_json(
        build_rewrite_prompt(text, budget, syllables), REWRITE_SCHEMA
    )
    new_text = (answer.get("text") or "").strip()
    if not new_text:
        raise ValueError("LLM trả về câu rỗng khi được yêu cầu viết lại ngắn hơn")
    return new_text
