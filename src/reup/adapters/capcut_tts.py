"""TTS thật qua CapCut.

Ba điều học được lúc khảo sát, đều nằm trong code dưới đây:

1. `rate` không có tác dụng (đo: rate 1.5 chỉ ngắn 5.9%). Luôn gửi 1.0 và để
   `fit` lo bằng viết lại + `atempo`.
2. Server cache theo text. Gọi lại cùng câu là miễn phí và tức thì — nên chạy
   lại job không tốn gì, nhưng cũng có nghĩa **đổi rate không đổi được độ dài**.
3. API gãy sau khoảng 15 request liên tiếp, nên phải thử lại có backoff.

Đi qua subprocess vì `capcut-tts-api` là repo riêng với venv Python 3.9 của nó.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable

from reup.adapters.tts import TTSResult, Voice
from reup.media.audio import duration_ms, to_wav

DRIVER = Path(__file__).with_name("capcut_driver.py")

# rate không có tác dụng — hằng số này tồn tại để nói rõ đó là lựa chọn, không
# phải quên. Xem spec §7.7.
FIXED_RATE = "1.0"


class CapCutError(RuntimeError):
    pass


class CapCutTTS:
    def __init__(
        self,
        capcut_dir: Path,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
        poll_interval: float = 1.0,
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

    def _voice_table(self) -> list[dict]:
        self._check_installed()
        path = self.capcut_dir / "Voice.json"
        if not path.exists():
            raise CapCutError(f"không thấy {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def voices(self, lang: str) -> list[Voice]:
        return [
            Voice(id=v["voice_type"], name=v["display_name"], lang=v["lan"])
            for v in self._voice_table()
            if v["lan"] == lang
        ]

    def synthesize(self, text: str, lang: str, voice: str, out: Path) -> TTSResult:
        table = {v["voice_type"]: v for v in self._voice_table()}
        if voice not in table:
            raise ValueError(
                f"giọng {voice!r} không có trong Voice.json. "
                f"Có sẵn: {sorted(table)}"
            )

        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        mp3 = out.with_suffix(".mp3")

        from reup.text import sanitize_for_tts

        clean_text = sanitize_for_tts(text)
        if not clean_text or not any(ch.isalnum() for ch in clean_text):
            from reup.media.audio import silence
            silence(out, 500)
            return TTSResult(path=out, actual_ms=500)

        cmd = [
            str(self._python), str(DRIVER),
            "--capcut-dir", str(self.capcut_dir),
            "--text", clean_text,
            "--out", str(mp3),
            "--voice", voice,
            "--resource-id", table[voice]["resource_id"],
            "--rate", FIXED_RATE,
            "--poll-interval", str(self.poll_interval),
        ]

        last = ""
        for attempt in range(self.retries):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15.0)
            except subprocess.TimeoutExpired:
                last = "Driver timeout sau 15s"
            else:
                if proc.returncode == 0:
                    to_wav(mp3, out)
                    mp3.unlink(missing_ok=True)
                    return TTSResult(path=out, actual_ms=duration_ms(out))
                last = (proc.stderr or proc.stdout).strip()
            if attempt < self.retries - 1:
                self.sleep(2**attempt)

        raise CapCutError(
            f"CapCut TTS hỏng sau {self.retries} lần thử với câu {text[:60]!r}: {last}"
        )
