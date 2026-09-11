"""Gemini qua google-genai.

Hai loại lỗi, hai ngân sách thử lại riêng (spec §12):
- lỗi mạng: thử lại 3 lần, backoff lũy thừa
- JSON sai cấu trúc: thử lại 2 lần, **kèm thông báo lỗi vào prompt** để model
  biết nó sai ở đâu; không kèm thì nó trả lại y hệt.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Callable

from reup.adapters.llm import LLMError

API_KEY_ENV = "GEMINI_API_KEY"
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class MissingAPIKey(LLMError):
    pass


def _strip_fence(text: str) -> str:
    m = _FENCE.match(text or "")
    return m.group(1) if m else (text or "")


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
                last = exc
                if attempt < self.net_retries - 1:
                    self.sleep(2**attempt)
        raise LLMError(f"Gemini lỗi mạng sau {self.net_retries} lần thử: {last}") from last

    def complete_json(self, prompt: str, schema: dict) -> dict:
        current = prompt
        last_err = ""
        for _ in range(self.json_retries + 1):
            raw = self._call_once(current, schema)
            try:
                parsed = json.loads(_strip_fence(raw))
            except json.JSONDecodeError as exc:
                last_err = str(exc)
            else:
                if isinstance(parsed, dict):
                    return parsed
                last_err = f"cần một object JSON, nhận {type(parsed).__name__}"

            current = (
                f"{prompt}\n\n"
                "--- Lần trước bạn trả về thứ không đọc được ---\n"
                f"Trả về: {raw[:500]}\n"
                f"Lỗi: {last_err}\n"
                "Lần này chỉ trả về JSON hợp lệ, không kèm chữ nào khác, "
                "không bọc trong ```."
            )
        raise LLMError(
            f"Gemini không trả được JSON hợp lệ sau {self.json_retries + 1} lần: {last_err}"
        )
