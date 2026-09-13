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


def contains_cjk(text: str) -> bool:
    return bool(_HAN.search(text or ""))


_COMMON_CJK_MAP = {
    "喂": "A lô",
    "嗯": "Ừm",
    "啊": "A",
    "哦": "Ồ",
    "呀": "Nha",
    "吧": "Đi",
    "哈": "Ha",
    "大爷": "Bác",
    "老板": "Ông chủ",
    "电动车": "Xe điện",
    "骑过来": "Lái qua đây",
    "骑": "Lái",
    "你": "Bạn",
    "我": "Tôi",
    "他": "Anh ấy",
    "她": "Cô ấy",
}


def sanitize_for_tts(text: str) -> str:
    """Làm sạch câu trước khi đưa vào bộ đọc TTS tiếng Việt.

    Loại bỏ hoặc phiên âm các ký tự chữ Hán/CJK còn sót lại, xóa các ký tự lạ
    để CapCut TTS không bị từ chối với lỗi TTSInvalidText.
    """
    if not text:
        return ""
    cleaned = text
    for k, v in _COMMON_CJK_MAP.items():
        cleaned = cleaned.replace(k, v)
    # Xoá tất cả chữ Hán còn lại
    cleaned = _HAN.sub("", cleaned)
    # Xoá các ký tự bullet hoặc ký hiệu đặc biệt
    cleaned = re.sub(r"[•|/\\~#^*_+=<>{}\[\]]", " ", cleaned)
    # Gom khoảng trắng
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
