"""ASR qua CapCut, thay cho Whisper local.

Vì sao tồn tại: Whisper chiếm Neural Engine vài giây mỗi clip. Trên Air 16GB,
chạy song song với Demucs là hết RAM. CapCut STT đẩy việc lên mạng — chậm hơn
nhưng không tốn gì của máy (spec R5). Đổi bằng `asr.engine` trong config.

Hai khác biệt so với Whisper, đều lộ ra ở đây:

1. **Không tự nhận ngôn ngữ.** API bắt khai `language`. `--lang auto` vì thế
   phải báo lỗi chứ không đoán bừa — đoán sai thì cả transcript thành rác.
2. **Ăn mp3, không ăn wav 16k của pipeline.** Nén lại trước khi upload.
3. **Trả mảnh phụ đề, không trả câu.** Đo thật một câu 2.4 giây: CapCut cắt
   thành năm mảnh 2-3 chữ ("hôm nay" / "tôi sẽ dạy" / "các bạn" / ...) vì bên
   trong nó là bộ sinh phụ đề, `words_per_line = 15`, `max_lines = 1`. Whisper
   trả câu. Để mảnh nguyên như vậy thì ngân sách âm tiết của `translate` tính
   trên khe 200 ms — ra trần 1 âm tiết — và LLM dịch từng cụm rời không có ngữ
   pháp. Nên `merge_utterances` ghép lại thành câu trước khi trả về.

Đi qua subprocess vì `capcut-tts-api` là repo riêng với venv Python 3.9 của nó.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable

from reup.adapters.capcut_tts import CapCutError
from reup.media.audio import to_mp3
from reup.models import Segment
from reup.text import _HAN

DRIVER = Path(__file__).with_name("capcut_stt_driver.py")

# Khoảng lặng đủ dài để coi là hết câu. Đo trên mẫu thật: trong một câu liền
# mạch các mảnh dính nhau đúng 0 ms, nên ngưỡng nào trong khoảng 200-500 ms cũng
# cho cùng kết quả; 400 ms là chỗ giữa.
MAX_GAP_MS = 400
# Trần độ dài một câu. Người nói liên tục không nghỉ thì vẫn phải cắt, nếu không
# `fit` nhận một khe 40 giây và phụ đề thành một khối chữ.
MAX_SEGMENT_MS = 7000
# Dấu kết câu: có thì cắt luôn, không cần chờ khoảng lặng.
SENTENCE_END = ".!?。！？…"

# CapCut dùng mã đầy đủ; phần còn lại của repo dùng mã hai chữ như Whisper.
LANGS = {
    "zh": "zh-CN",
    "en": "en-US",
    "vi": "vi-VN",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "th": "th-TH",
    "id": "id-ID",
}


def _join(left: str, right: str) -> str:
    """Nối hai mảnh. Tiếng Trung viết liền, tiếng Việt và tiếng Anh cách chữ."""
    if _HAN.match(left[-1:]) or _HAN.match(right[:1]):
        return left + right
    return f"{left} {right}"


def merge_utterances(
    utterances: list[dict],
    max_gap_ms: int = MAX_GAP_MS,
    max_ms: int = MAX_SEGMENT_MS,
) -> list[dict]:
    """Ghép mảnh phụ đề liền nhau thành câu.

    Cắt khi: có khoảng lặng dài hơn `max_gap_ms`, mảnh trước kết bằng dấu câu,
    hoặc câu đang dựng đã dài quá `max_ms`.
    """
    merged: list[dict] = []
    for raw in utterances:
        text = (raw.get("text") or "").strip()
        if not text:
            continue
        start, end = int(raw["start_ms"]), int(raw["end_ms"])
        if merged:
            prev = merged[-1]
            gap = start - prev["end_ms"]
            too_long = end - prev["start_ms"] > max_ms
            ended = prev["text"][-1:] in SENTENCE_END
            if gap <= max_gap_ms and not too_long and not ended:
                prev["text"] = _join(prev["text"], text)
                prev["end_ms"] = end
                continue
        merged.append({"text": text, "start_ms": start, "end_ms": end})
    return merged


class CapCutSTT:
    name = "capcut"

    def __init__(
        self,
        capcut_dir: Path,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
        poll_interval: float = 2.0,
    ) -> None:
        self.capcut_dir = Path(capcut_dir)
        self.sleep = sleep
        self.retries = retries
        self.poll_interval = poll_interval

    @property
    def _python(self) -> Path:
        return self.capcut_dir / ".venv" / "bin" / "python"

    def _check_installed(self) -> None:
        if not self.capcut_dir.is_dir():
            raise CapCutError(
                f"không thấy thư mục CapCut ở {self.capcut_dir}. "
                "Sửa `tts.capcut_dir` trong config.toml"
            )
        if not self._python.exists():
            raise CapCutError(
                f"không thấy interpreter {self._python}. "
                "Thư mục CapCut phải có venv riêng của nó"
            )

    @staticmethod
    def capcut_lang(lang: str | None) -> str:
        if lang in (None, "", "auto"):
            raise ValueError(
                "CapCut STT không tự nhận ngôn ngữ — phải khai rõ. "
                "Tạo job bằng `reup add <url> --lang zh`, hoặc đặt "
                "`asr.engine = \"whisper\"` trong config.toml"
            )
        assert lang is not None
        if lang in LANGS:
            return LANGS[lang]
        if "-" in lang:  # người dùng đã tự khai mã đầy đủ
            return lang
        raise ValueError(
            f"CapCut STT chưa biết ngôn ngữ {lang!r}. Có sẵn: {sorted(LANGS)}"
        )

    def transcribe(
        self, audio: Path, lang: str | None
    ) -> tuple[str, list[Segment]]:
        self._check_installed()
        language = self.capcut_lang(lang)

        audio = Path(audio)
        mp3 = audio.with_suffix(".stt.mp3")
        to_mp3(audio, mp3)
        try:
            payload = self._drive(mp3, language)
        finally:
            mp3.unlink(missing_ok=True)

        # Ghép trước rồi mới đánh số: id phải liền mạch, mảnh rỗng không để lỗ.
        spoken = merge_utterances(payload.get("utterances") or [])
        segments = [
            Segment(
                id=idx,
                start_ms=int(u["start_ms"]),
                end_ms=int(u["end_ms"]),
                text=u["text"].strip(),
                text_source="asr",
                confidence=1.0,
            )
            for idx, u in enumerate(spoken, start=1)
        ]
        return payload.get("language") or language, segments

    def _drive(self, mp3: Path, language: str) -> dict:
        cmd = [
            str(self._python), str(DRIVER),
            "--capcut-dir", str(self.capcut_dir),
            "--audio", str(mp3),
            "--language", language,
            "--poll-interval", str(self.poll_interval),
        ]

        last = ""
        for attempt in range(self.retries):
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                try:
                    return json.loads(proc.stdout.strip().splitlines()[-1])
                except (json.JSONDecodeError, IndexError) as exc:
                    raise CapCutError(
                        f"driver STT in ra thứ không đọc được: {proc.stdout[:200]!r}"
                    ) from exc
            last = (proc.stderr or proc.stdout).strip()
            if attempt < self.retries - 1:
                self.sleep(2**attempt)

        raise CapCutError(f"CapCut STT hỏng sau {self.retries} lần thử: {last}")
