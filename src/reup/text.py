"""Đếm âm tiết theo ngôn ngữ.

Dùng cho hai việc: StubTTS bịa độ dài, và stage translate (phase 2)
tính trần âm tiết cho mỗi khe thời gian.
"""
from __future__ import annotations

import re
import unicodedata

_HAN = re.compile(r"[㐀-䶿一-鿿]")
_EN_VOWEL_GROUP = re.compile(r"[aeiouy]+")


def _has_letter(token: str) -> bool:
    return any(unicodedata.category(ch).startswith("L") for ch in token)


def count_syllables(text: str, lang: str) -> int:
    if lang == "zh":
        return len(_HAN.findall(text))
    if lang == "en":
        total = 0
        for token in text.lower().split():
            groups = len(_EN_VOWEL_GROUP.findall(token))
            total += groups if groups else (1 if _has_letter(token) else 0)
        return total
    # vi và mọi ngôn ngữ khác: âm tiết viết tách rời bằng khoảng trắng
    return sum(1 for token in text.split() if _has_letter(token))
