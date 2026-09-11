"""Luật hợp nhất ASR và OCR — spec §7.5. Hàm thuần, không gọi LLM.

Không để LLM tự do trộn hai văn bản: đó là cách nhanh nhất để nó bịa. Luật:

1. Ghép theo chồng lấn thời gian.
2. Khớp trên 80% ký tự  ->  lấy **chữ của OCR, giờ của ASR**. Không gọi LLM.
3. Lệch nhiều  ->  đưa LLM **chọn một trong hai**, cấm viết mới.
4. OCR rỗng  ->  dùng thẳng ASR.

OCR thắng khi khớp vì hardsub là chữ do chính tác giả gõ, còn ASR là máy đoán
từ âm thanh. Nhưng mốc thời gian thì lấy của ASR: OCR chỉ biết chữ xuất hiện ở
khung nào, độ chính xác bằng bước lấy mẫu (500ms), còn ASR cho mốc theo từ.
"""
from __future__ import annotations

import difflib
import re

MATCH_THRESHOLD = 0.8
_NOISE = re.compile(r"[\s\.,!?;:'\"“”‘’…\-–—()\[\]]+")


def normalize(text: str) -> str:
    """Bỏ dấu câu và khoảng trắng để so hai bên cho công bằng."""
    return _NOISE.sub("", (text or "").lower())


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def overlap_ms(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def best_ocr_line(segment, lines: list[dict]) -> dict | None:
    """Dòng OCR phủ lên đoạn ASR nhiều nhất theo thời gian."""
    best, best_overlap = None, 0
    for line in lines:
        o = overlap_ms(
            segment.start_ms, segment.end_ms, line["start_ms"], line["end_ms"]
        )
        if o > best_overlap:
            best, best_overlap = line, o
    return best


def decide(segment, line: dict | None) -> tuple[str, str, list[str]]:
    """Trả (text, text_source, flags) cho một đoạn. `text_source == "llm"` nghĩa
    là chưa quyết được, chỗ gọi phải hỏi LLM chọn một trong hai."""
    if line is None or not normalize(line["text"]):
        return segment.text, "asr", []

    score = similarity(segment.text, line["text"])
    if score >= MATCH_THRESHOLD:
        # Khớp: tin chữ của OCR, giữ giờ của ASR.
        return line["text"], "ocr", []
    return "", "llm", ["asr_ocr_mismatch"]
