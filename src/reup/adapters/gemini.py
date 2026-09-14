"""Gemini qua google-genai.

Hai loại lỗi, hai ngân sách thử lại riêng (spec §12):
- lỗi mạng: thử lại 3 lần, backoff lũy thừa
- JSON sai cấu trúc: thử lại 2 lần, **kèm thông báo lỗi vào prompt** để model
  biết nó sai ở đâu; không kèm thì nó trả lại y hệt.
"""
from __future__ import annotations

import os
import re
import time
from typing import Callable

from reup.adapters.llm import LLMError, complete_json_with_repair

API_KEY_ENV = "GEMINI_API_KEY"
_RETRY_DELAY = re.compile(r"[\'\"]retryDelay[\'\"]:\s*[\'\"](\d+)s")
# Chờ lâu hơn mức này thì thà báo lỗi còn hơn treo job.
MAX_BACKOFF_S = 65.0


def _is_daily_quota(message: str) -> bool:
    """Hạn mức theo ngày khác hạn mức theo phút: chờ không giải quyết được gì."""
    return "PerDay" in message or "per day" in message.lower()


def _suggested_delay(message: str) -> float | None:
    """Gemini báo sẵn phải chờ bao lâu khi bị giới hạn tốc độ."""
    m = _RETRY_DELAY.search(message)
    if not m:
        return None
    return min(float(m.group(1)) + 1, MAX_BACKOFF_S)


class MissingAPIKey(LLMError):
    pass


class QuotaExhausted(LLMError):
    """Hết hạn mức theo NGÀY — thử lại trong cùng ngày cũng vô ích."""


class GeminiLLM:
    def __init__(
        self,
        model: str,
        client=None,
        sleep: Callable[[float], None] = time.sleep,
        net_retries: int = 3,
        json_retries: int = 2,
    ) -> None:
        self.model = model
        self.sleep = sleep
        self.net_retries = net_retries
        self.json_retries = json_retries
        if client is not None:
            self.client = client
            return

        key = os.environ.get(API_KEY_ENV)
        if not key:
            raise MissingAPIKey(
                f"chưa có API key. Đặt biến môi trường {API_KEY_ENV} rồi chạy lại. "
                "Lấy key ở https://aistudio.google.com/apikey"
            )
        try:
            from google import genai
        except ImportError as exc:
            raise LLMError(
                "thiếu package google-genai. Cài bằng `uv pip install google-genai`"
            ) from exc
        self.client = genai.Client(api_key=key)

    def _call_once(self, prompt: str, schema: dict) -> str:
        """Một lượt gọi, chỉ lo lỗi mạng."""
        last: Exception | None = None
        for attempt in range(self.net_retries):
            try:
                resp = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": schema,
                    },
                )
                return resp.text
            except Exception as exc:  # mạng rớt, quá tải, hết hạn mức
                text = str(exc)
                if "429" in text and _is_daily_quota(text):
                    raise QuotaExhausted(
                        "hết hạn mức Gemini trong ngày cho model "
                        f"{self.model!r}. Gói miễn phí chỉ cho 20 request/ngày "
                        "mỗi model.\n"
                        "Cách đi tiếp: đợi sang ngày mới, đổi llm.model sang "
                        "model khác trong config.toml, hoặc bật thanh toán ở "
                        "https://aistudio.google.com/apikey"
                    ) from exc
                last = exc
                if attempt < self.net_retries - 1:
                    # Gemini báo sẵn phải chờ bao lâu; backoff 1s rồi 2s của ta
                    # ngắn hơn nhiều nên thử lại lúc đó chắc chắn lại 429.
                    self.sleep(_suggested_delay(text) or 2**attempt)
        raise LLMError(f"Gemini lỗi mạng sau {self.net_retries} lần thử: {last}") from last

    def complete_json(self, prompt: str, schema: dict) -> dict:
        return complete_json_with_repair(
            lambda p: self._call_once(p, schema),
            prompt,
            retries=self.json_retries,
            who="Gemini",
        )
