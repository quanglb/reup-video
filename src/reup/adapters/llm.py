"""Biên giới ra mô hình ngôn ngữ.

Bốn chỗ dùng: hợp nhất ASR/OCR, dịch có ngân sách âm tiết, viết lại cho vừa
khe, và sinh metadata lúc export. Cả bốn đều cần JSON đúng cấu trúc chứ không
phải văn xuôi, nên interface chỉ có một hàm và nó luôn trả dict.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Protocol


class LLMError(RuntimeError):
    pass


class LLMAdapter(Protocol):
    def complete_json(self, prompt: str, schema: dict) -> dict: ...


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def strip_fence(text: str) -> str:
    """Bỏ ```json ... ``` quanh câu trả lời. Model nào cũng thỉnh thoảng bọc."""
    m = _FENCE.match(text or "")
    return m.group(1) if m else (text or "")


def complete_json_with_repair(
    call_once: Callable[[str], str],
    prompt: str,
    retries: int,
    who: str,
) -> dict:
    """Gọi `call_once` tới khi ra một object JSON, tối đa `retries` lần sửa.

    Lần thử lại **kèm thông báo lỗi vào prompt** để model biết nó sai ở đâu;
    không kèm thì nó trả lại y hệt (spec §12).

    Dùng chung cho mọi provider: model chạy local còn hay trả JSON hỏng hơn
    model cloud, nên đây là chỗ cuối cùng nên chép đi chép lại.
    """
    current = prompt
    last_err = ""
    for _ in range(retries + 1):
        raw = call_once(current)
        try:
            parsed = json.loads(strip_fence(raw))
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
        f"{who} không trả được JSON hợp lệ sau {retries + 1} lần: {last_err}"
    )
