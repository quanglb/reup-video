"""Ngân sách âm tiết cho một khe thời gian — hàm thuần, không gọi LLM.

Tách khỏi stage vì đây là con số quyết định bản dịch dài bao nhiêu, và vì
`fit` phải tính lại đúng con số đó khi bắt viết lại.
"""
from __future__ import annotations

from reup.text import count_syllables

# Tiếng Việt đọc khoảng 4.5 âm tiết/giây (spec §7.6).
# Đo thật trên CapCut: 10 âm tiết trong 2.28s = 225 ms/âm tiết = 4.44/giây.
DEFAULT_SYLLABLE_RATE = 4.5


def syllable_budget(slot_ms: int, rate: float = DEFAULT_SYLLABLE_RATE) -> int:
    """Số âm tiết tối đa đọc vừa khe `slot_ms`.

    Làm tròn xuống: cấp thừa một âm tiết là đẩy đoạn ra ngoài khe, còn thiếu
    một âm tiết chỉ để lại khoảng lặng ngắn mà `fit` đệm được.
    """
    if slot_ms <= 0:
        raise ValueError(f"slot_ms phải dương, nhận {slot_ms}")
    return max(1, int(slot_ms / 1000 * rate))


def fits_budget(text: str, budget: int) -> bool:
    return count_syllables(text, "vi") <= budget
