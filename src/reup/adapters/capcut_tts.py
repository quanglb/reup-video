"""TTS thật qua CapCut.

Ba điều học được lúc khảo sát, đều nằm trong code dưới đây:

1. `rate` không có tác dụng (đo: rate 1.5 chỉ ngắn 5.9%). Luôn gửi 1.0 và để
   `fit` lo bằng viết lại + `atempo`.
2. Server cache theo text. Gọi lại cùng câu là miễn phí và tức thì — nên chạy
   lại job không tốn gì, nhưng cũng có nghĩa **đổi rate không đổi được độ dài**.
3. API gãy sau khoảng 15 request liên tiếp, nên batch nhiều câu vào 1 request
   và có nghỉ nhịp chủ động / retry backoff.

Đi qua subprocess vì `capcut-tts-api` là repo riêng với venv Python 3.9 của nó.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from reup.adapters.tts import TTSResult, Voice
from reup.media.audio import duration_ms, to_wav

DRIVER = Path(__file__).with_name("capcut_driver.py")

logger = logging.getLogger(__name__)

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
        max_polls: int = 10,
        pause_every: int = 12,
        pause_seconds: float = 2.0,
        allow_edge_fallback: bool = True,
    ) -> None:
        self.capcut_dir = Path(capcut_dir)
        self.sleep = sleep
        self.retries = retries
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        # <= 0 nghĩa là "không bao giờ nghỉ nhịp" — clamp về 0 để
        # `_count_success_and_maybe_pause` tắt hẳn nhánh `%` thay vì
        # ZeroDivisionError khi ai đó lỡ đặt pause_every = 0 trong config.
        self.pause_every = max(0, pause_every)
        self.pause_seconds = pause_seconds
        # tts.allow_edge_fallback trong config.toml. False thì driver không
        # được âm thầm đổi sang edge-tts nữa — lỗi phải nổi lên thành
        # CapCutError thật để retry đúng nghĩa (xem capcut_driver.py).
        self.allow_edge_fallback = allow_edge_fallback
        self._request_count = 0
        self._lock = threading.Lock()

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

    def _count_success_and_maybe_pause(self) -> None:
        """Đếm request thành công liên tục (thread-safe); nghỉ nhịp chủ động
        mỗi `pause_every` lần để không chạm ngưỡng gãy ~15 của server CapCut."""
        with self._lock:
            self._request_count += 1
            should_pause = (
                self.pause_every > 0 and self._request_count % self.pause_every == 0
            )
        if should_pause:
            self.sleep(self.pause_seconds)

    @staticmethod
    def _parse_engine(stdout: str) -> str:
        """Đọc trường `engine` từ JSON in ra bởi driver.

        Mặc định "capcut" nếu thiếu (driver cũ chưa có trường này) hoặc nếu
        stdout không đọc được — không được để lỗi đọc JSON làm mất kết quả
        thành công thật."""
        try:
            payload = json.loads((stdout or "").strip().splitlines()[-1])
            if isinstance(payload, list) and payload:
                payload = payload[0]
        except (ValueError, IndexError):
            return "capcut"
        return str(payload.get("engine") or "capcut")

    def synthesize_batch(
        self, items: list[tuple[str, Path]], lang: str, voice: str
    ) -> list[TTSResult]:
        if not items:
            return []

        table = {v["voice_type"]: v for v in self._voice_table()}
        if voice not in table:
            raise ValueError(
                f"giọng {voice!r} không có trong Voice.json. "
                f"Có sẵn: {sorted(table)}"
            )

        from reup.text import sanitize_for_tts

        results: list[TTSResult | None] = [None] * len(items)
        non_empty_indices: list[int] = []

        for i, (text, out) in enumerate(items):
            out_p = Path(out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            clean_text = sanitize_for_tts(text)
            if not clean_text or not any(ch.isalnum() for ch in clean_text):
                from reup.media.audio import silence

                silence(out_p, 500)
                results[i] = TTSResult(path=out_p, actual_ms=500, engine="silence")
            else:
                non_empty_indices.append(i)

        if not non_empty_indices:
            return [r for r in results if r is not None]

        cmd = [
            str(self._python),
            str(DRIVER),
            "--capcut-dir",
            str(self.capcut_dir),
        ]
        for idx in non_empty_indices:
            text_i, out_i = items[idx]
            clean_text = sanitize_for_tts(text_i)
            mp3_i = Path(out_i).with_suffix(".mp3")
            cmd.extend(["--text", clean_text, "--out", str(mp3_i)])

        cmd.extend([
            "--voice",
            voice,
            "--resource-id",
            table[voice]["resource_id"],
            "--rate",
            FIXED_RATE,
            "--poll-interval",
            str(self.poll_interval),
            "--max-polls",
            str(self.max_polls),
            "--allow-fallback",
            "1" if self.allow_edge_fallback else "0",
        ])

        # Buffer: bù cho request POST tts-new và tải N file mp3
        buffer = 10.0 + len(non_empty_indices) * 2.0
        outer_timeout = self.max_polls * self.poll_interval + buffer

        last = ""
        batch_success = False
        stdout_text = ""
        for attempt in range(self.retries):
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=outer_timeout
                )
            except subprocess.TimeoutExpired:
                last = f"Driver timeout sau {outer_timeout}s"
            else:
                if proc.returncode == 0:
                    stdout_text = proc.stdout
                    batch_success = True
                    break
                last = (proc.stderr or proc.stdout).strip()
            if attempt < self.retries - 1:
                self.sleep(2**attempt)

        if batch_success:
            try:
                parsed_list = json.loads(stdout_text.strip().splitlines()[-1])
                if isinstance(parsed_list, dict):
                    parsed_list = [parsed_list]
            except Exception:
                parsed_list = []

            for i, idx in enumerate(non_empty_indices):
                text_i, out_i = items[idx]
                out_p = Path(out_i)
                mp3_i = out_p.with_suffix(".mp3")
                to_wav(mp3_i, out_p)
                mp3_i.unlink(missing_ok=True)
                p_item = parsed_list[i] if i < len(parsed_list) else {}
                engine = str(p_item.get("engine") or "capcut")
                if engine == "edge_tts_fallback":
                    logger.warning(
                        "CapCut TTS rơi xuống edge-tts fallback cho câu %r "
                        "(giọng %s, ra %s) — audio KHÔNG phải giọng CapCut thật",
                        text_i[:80],
                        voice,
                        out_p,
                    )
                results[idx] = TTSResult(
                    path=out_p, actual_ms=duration_ms(out_p), engine=engine
                )
            self._count_success_and_maybe_pause()
            return [r for r in results if r is not None]

        # Lưới an toàn: nếu batch > 1 câu bị fail toàn bộ, fallback gọi từng câu một
        if len(non_empty_indices) > 1:
            logger.warning(
                "CapCut TTS batch %d câu thất bại (%s), fallback gọi từng câu một",
                len(non_empty_indices),
                last,
            )
            for idx in non_empty_indices:
                text_i, out_i = items[idx]
                results[idx] = self._synthesize_single(text_i, lang, voice, out_i)
            return [r for r in results if r is not None]

        # Single câu fail
        first_text = items[non_empty_indices[0]][0]
        raise CapCutError(
            f"CapCut TTS hỏng sau {self.retries} lần thử với câu {first_text[:60]!r}: {last}"
        )

    def _synthesize_single(
        self, text: str, lang: str, voice: str, out: Path
    ) -> TTSResult:
        table = {v["voice_type"]: v for v in self._voice_table()}
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        mp3 = out.with_suffix(".mp3")

        from reup.text import sanitize_for_tts

        clean_text = sanitize_for_tts(text)
        if not clean_text or not any(ch.isalnum() for ch in clean_text):
            from reup.media.audio import silence

            silence(out, 500)
            return TTSResult(path=out, actual_ms=500, engine="silence")

        cmd = [
            str(self._python),
            str(DRIVER),
            "--capcut-dir",
            str(self.capcut_dir),
            "--text",
            clean_text,
            "--out",
            str(mp3),
            "--voice",
            voice,
            "--resource-id",
            table[voice]["resource_id"],
            "--rate",
            FIXED_RATE,
            "--poll-interval",
            str(self.poll_interval),
            "--max-polls",
            str(self.max_polls),
            "--allow-fallback",
            "1" if self.allow_edge_fallback else "0",
        ]

        buffer = 10.0 + 1 * 2.0
        outer_timeout = self.max_polls * self.poll_interval + buffer

        last = ""
        for attempt in range(self.retries):
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=outer_timeout
                )
            except subprocess.TimeoutExpired:
                last = f"Driver timeout sau {outer_timeout}s"
            else:
                if proc.returncode == 0:
                    engine = self._parse_engine(proc.stdout)
                    to_wav(mp3, out)
                    mp3.unlink(missing_ok=True)
                    self._count_success_and_maybe_pause()
                    if engine == "edge_tts_fallback":
                        logger.warning(
                            "CapCut TTS rơi xuống edge-tts fallback cho câu %r "
                            "(giọng %s, ra %s) — audio KHÔNG phải giọng CapCut thật",
                            text[:80],
                            voice,
                            out,
                        )
                    return TTSResult(
                        path=out, actual_ms=duration_ms(out), engine=engine
                    )
                last = (proc.stderr or proc.stdout).strip()
            if attempt < self.retries - 1:
                self.sleep(2**attempt)

        raise CapCutError(
            f"CapCut TTS hỏng sau {self.retries} lần thử với câu {text[:60]!r}: {last}"
        )

    def synthesize(self, text: str, lang: str, voice: str, out: Path) -> TTSResult:
        return self.synthesize_batch([(text, out)], lang, voice)[0]
