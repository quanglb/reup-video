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
from reup.translate_styles import (
    AUTO,
    DEFAULT_GENRE,
    GENRES,
    guess_genre,
    style_block,
    validate_genre,
)

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


BATCH_SIZE = 15


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "genre": {"type": "string"},
        "summary": {"type": "string"},
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["name", "role"],
            },
        },
        "address": {"type": "string"},
    },
    "required": ["genre", "summary", "characters", "address"],
}


def build_analysis_prompt(transcript: Transcript, info: dict, forced_genre: str | None) -> str:
    """Đọc cả video một lượt trước khi dịch: thể loại, cốt truyện, nhân vật, xưng hô.

    Dịch theo batch 15 câu thì mỗi batch chỉ thấy hai câu hàng xóm — không có
    bảng nhân vật chung là batch này 'anh/em', batch sau 'tôi/bạn'.
    """
    genre_lines = "\n".join(f"- {g.key}: {g.label}" for g in GENRES.values())
    genre_req = (
        f"Thể loại đã chốt là '{forced_genre}', trả đúng khoá đó ở trường genre."
        if forced_genre
        else f"Chọn MỘT khoá thể loại hợp nhất:\n{genre_lines}"
    )
    lines = "\n".join(f"{s.id}. {s.text}" for s in transcript.segments)
    return (
        "Bạn chuẩn bị lồng tiếng Việt cho video ngắn dưới đây. Chưa dịch — "
        "hãy phân tích bối cảnh trước.\n\n"
        f"Tiêu đề gốc: {info.get('title') or '(không có)'}\n"
        f"Mô tả gốc: {info.get('description') or '(không có)'}\n\n"
        f"Lời thoại (ASR/OCR, có thể sai hoặc lặp):\n{lines}\n\n"
        f"{genre_req}\n\n"
        "Trả về JSON:\n"
        '- "genre": khoá thể loại\n'
        '- "summary": 1-2 câu tiếng Việt tóm tắt chuyện gì xảy ra\n'
        '- "characters": các nhân vật/người nói, name là cách gọi tiếng Việt '
        "(vd 'chàng trai', 'bà cụ', 'tên lừa đảo 1'), role là vai và tuổi tương đối\n"
        '- "address": bảng xưng hô tiếng Việt giữa các cặp nhân vật, '
        "vd 'Chàng trai ↔ bà cụ: cháu/bà; chàng trai ↔ thợ sửa xe: em/anh, khi nổi "
        "giận: tôi/anh'"
    )


def analyze(transcript: Transcript, info: dict, llm: LLMAdapter, forced_genre: str | None) -> dict:
    answer = llm.complete_json(
        build_analysis_prompt(transcript, info, forced_genre), ANALYSIS_SCHEMA
    )
    genre = forced_genre
    source = "config"
    if genre is None:
        genre = str(answer.get("genre") or "").strip()
        source = "llm"
        if genre not in GENRES:
            genre, source = guess_genre(info), "metadata"

    parts = []
    if answer.get("summary"):
        parts.append(f"- Nội dung: {answer['summary']}")
    chars = [c for c in answer.get("characters") or [] if isinstance(c, dict)]
    if chars:
        parts.append(
            "- Nhân vật: "
            + "; ".join(f"{c.get('name', '')} ({c.get('role', '')})" for c in chars)
        )
    if answer.get("address"):
        parts.append(f"- Xưng hô: {answer['address']}")
    return {"genre": genre, "genre_source": source, "context": "\n".join(parts)}


def _style_path(job: Job):
    return job.root / "translate_style.json"


def load_style_hint(job: Job) -> str:
    """Luật văn phong đã chốt lúc dịch, cho vòng viết lại ở `fit` dùng lại."""
    path = _style_path(job)
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    return style_block(data.get("genre", DEFAULT_GENRE), data.get("context", ""))


def build_prompt(
    transcript: Transcript,
    budgets: dict[int, int],
    batch_indices: list[int] | None = None,
    style: str = "",
) -> str:
    """Gọi dịch kèm ngữ cảnh câu trước và câu sau.

    Hỗ trợ chia theo batch nhỏ (10-15 câu) để không vượt trần output token
    của local LLM với video dài nhiều câu thoại, đồng thời vẫn giữ ngữ cảnh.
    """
    src = _LANG_NAMES.get(transcript.source_lang, transcript.source_lang)
    lines = []
    segs = transcript.segments
    indices = batch_indices if batch_indices is not None else list(range(len(segs)))
    for i in indices:
        seg = segs[i]
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
        "- DỊCH HOÀN TOÀN 100% SANG TIẾNG VIỆT (chữ Latin). Tuyệt đối KHÔNG giữ lại "
        "chữ Hán / tiếng Trung nào (ví dụ: '喂' -> 'A lô', '嗯' -> 'Ừm', '大爷' -> 'Bác/Ông').\n"
        "- Bỏ qua chữ rác, watermark hoặc quảng cáo vô nghĩa nếu có.\n"
        "- Viết để **nằm trong trần ngay từ đầu**. Đừng dịch dài rồi cắt cụt.\n"
        "- Tiếng Việt tính âm tiết theo tiếng, mỗi tiếng cách nhau bằng khoảng trắng. "
        '"thịt kho tàu" là 3 âm tiết.\n'
        "- Vượt trần thì bỏ chữ đệm và chi tiết phụ trước, giữ ý chính và xưng hô.\n"
        "- Giữ đại từ và văn phong nhất quán giữa các đoạn — dùng câu trước và "
        "câu sau làm ngữ cảnh, nhưng **chỉ dịch phần in đậm**.\n\n"
        + (f"{style}\n" if style else "")
        +
        "Trả về JSON: {\"segments\": [{\"id\": <số>, \"text\": \"<bản dịch>\"}]}. "
        "Phải có đủ mọi id được yêu cầu ở trên, không thêm id nào lạ.\n\n"
        + "\n\n".join(lines)
    )


def run_with(job: Job, cfg: Config, llm: LLMAdapter) -> None:
    from reup.text import sanitize_for_tts

    transcript = Transcript.load(job.transcript_json)
    if not transcript.segments:
        raise ValueError(f"{job.transcript_json} không có câu nào để dịch")

    budgets = {s.id: syllable_budget(s.slot_ms) for s in transcript.segments}
    all_segs = transcript.segments
    by_id: dict[int, str] = {}

    info = {}
    if job.source_info.exists():
        info = json.loads(job.source_info.read_text(encoding="utf-8"))
    forced = job.overrides.get("genre") or cfg.translate.genre
    forced = None if forced == AUTO else validate_genre(forced, "overrides.genre")
    chosen = analyze(transcript, info, llm, forced)
    atomic_write(_style_path(job), json.dumps(chosen, ensure_ascii=False, indent=2))
    style = style_block(chosen["genre"], chosen["context"])

    for i in range(0, len(all_segs), BATCH_SIZE):
        batch_indices = list(range(i, min(i + BATCH_SIZE, len(all_segs))))
        prompt = build_prompt(transcript, budgets, batch_indices=batch_indices, style=style)
        answer = llm.complete_json(prompt, RESPONSE_SCHEMA)
        for item in answer.get("segments", []):
            if item.get("id") is not None and item.get("text"):
                by_id[int(item["id"])] = item["text"].strip()

    missing = [s.id for s in transcript.segments if s.id not in by_id]
    if missing:
        missing_indices = [idx for idx, s in enumerate(all_segs) if s.id in missing]
        retry_prompt = build_prompt(
            transcript, budgets, batch_indices=missing_indices, style=style
        )
        retry_ans = llm.complete_json(retry_prompt, RESPONSE_SCHEMA)
        for item in retry_ans.get("segments", []):
            if item.get("id") is not None and item.get("text"):
                by_id[int(item["id"])] = item["text"].strip()

        still_missing = [s.id for s in transcript.segments if s.id not in by_id]
        if still_missing:
            raise ValueError(
                f"bản dịch thiếu đoạn {still_missing} — mất câu còn tệ hơn dịch sai, không chạy tiếp"
            )

    segments = []
    for seg in transcript.segments:
        raw_text = by_id[seg.id]
        text = sanitize_for_tts(raw_text) or "..."
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

    run_with(job, cfg, make_llm(cfg, "translate"))


# Chốt A đứng sau đây: duyệt bản dịch TRƯỚC khi tốn TTS và render (spec §8.2).
SPEC = StageSpec(
    name="translate", produces=("translation.json",), run=run, gate="a"
)


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


REWRITE_BATCH_SCHEMA = {
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


def build_rewrite_batch_prompt(items: list[tuple[int, str, int]], style: str = "") -> str:
    """`items`: (id câu, chữ hiện tại, trần âm tiết). `style`: luật thể loại
    đã dùng lúc dịch — viết lại mà quên nó thì xưng hô và giọng bị lệch."""
    blocks = []
    for seg_id, text, budget in items:
        blocks.append(
            f"### Đoạn {seg_id}\n"
            f"- Câu hiện tại ({count_syllables(text, TARGET_LANG)} âm tiết): {text}\n"
            f"- Trần cho phép: {budget} âm tiết"
        )
    return (
        "Những câu tiếng Việt dưới đây đều dài quá khe thời gian của chúng khi "
        "đọc lên.\n\n"
        + "\n\n".join(blocks)
        + "\n\nViết lại từng câu ngắn hơn, giữ đúng ý chính, vẫn là văn nói tự "
        "nhiên.\nBỏ được chi tiết phụ thì bỏ. Đừng cắt cụt giữa câu.\n"
        "Tiếng Việt tính âm tiết theo tiếng, cách nhau bằng khoảng trắng.\n"
        "Giữ nguyên id của từng đoạn, giữ xưng hô và giọng điệu của câu cũ.\n\n"
        + (f"{style}\n" if style else "")
        + 'Trả về JSON: {"segments": [{"id": <id>, "text": "<câu đã viết lại>"}]}'
    )


def rewrite_shorter_batch(
    llm: LLMAdapter, items: list[tuple[int, str, int]], style: str = ""
) -> dict[int, str]:
    """Viết lại NHIỀU câu theo từng đợt nhỏ. Trả {id: câu mới}."""
    if not items:
        return {}
    out: dict[int, str] = {}
    batch_size = 15
    for i in range(0, len(items), batch_size):
        chunk = items[i : i + batch_size]
        answer = llm.complete_json(
            build_rewrite_batch_prompt(chunk, style), REWRITE_BATCH_SCHEMA
        )
        for row in answer.get("segments") or []:
            if "text" in row:
                text = (row.get("text") or "").strip()
                if not text:
                    raise ValueError(f"LLM trả về câu rỗng cho đoạn {row.get('id')}")
                if row.get("id") is not None:
                    out[int(row["id"])] = text
    return out

