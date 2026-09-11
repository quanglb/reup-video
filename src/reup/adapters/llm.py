"""Biên giới ra mô hình ngôn ngữ.

Hai chỗ dùng: hợp nhất ASR/OCR (phase 3) và dịch có ngân sách âm tiết (phase 2).
Cả hai đều cần JSON đúng cấu trúc chứ không phải văn xuôi, nên interface chỉ có
một hàm và nó luôn trả dict.
"""
from __future__ import annotations

from typing import Protocol


class LLMError(RuntimeError):
    pass


class LLMAdapter(Protocol):
    def complete_json(self, prompt: str, schema: dict) -> dict: ...
