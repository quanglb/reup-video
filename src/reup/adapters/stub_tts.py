"""TTS giả: sinh wav im lặng dài theo số âm tiết.

Nhờ nó mà cả pipeline và toàn bộ test chạy được trước khi nối
source CapCut thật. Nếu chỗ nối TTS có vấn đề, mọi phần khác đã xong.
"""
from __future__ import annotations

from pathlib import Path

from reup.adapters.tts import TTSResult, Voice
from reup.media.audio import silence
from reup.text import count_syllables

_VOICES = [
    Voice(id="stub-vi-1", name="Giọng giả nữ", lang="vi"),
    Voice(id="stub-vi-2", name="Giọng giả nam", lang="vi"),
    Voice(id="stub-zh-1", name="Giọng giả tiếng Trung", lang="zh"),
    Voice(id="stub-en-1", name="Giọng giả tiếng Anh", lang="en"),
]

MIN_MS = 200


class StubTTS:
    def __init__(self, ms_per_syllable: int = 220) -> None:
        self.ms_per_syllable = ms_per_syllable

    def voices(self, lang: str) -> list[Voice]:
        return [v for v in _VOICES if v.lang == lang]

    def synthesize(self, text: str, lang: str, voice: str, out: Path) -> TTSResult:
        # Nhận mọi voice id, kể cả của CapCut. StubTTS phải đứng thay được
        # CapCut trong mọi cấu hình; giữ danh sách giọng riêng ở đây sẽ làm
        # `tts.voice = "BV074_streaming"` gãy chỉ vì đổi engine sang stub.
        if not voice:
            raise ValueError("thiếu tên giọng: truyền voice rỗng")
        ms = max(MIN_MS, count_syllables(text, lang) * self.ms_per_syllable)
        silence(out, ms)
        return TTSResult(path=Path(out), actual_ms=ms, engine="stub")

    def synthesize_batch(
        self, items: list[tuple[str, Path]], lang: str, voice: str
    ) -> list[TTSResult]:
        return [self.synthesize(text, lang, voice, out) for text, out in items]
