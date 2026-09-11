"""LLM phát lại từ băng ghi — để test không gọi mạng và không đổi kết quả.

Ghi một lần vào file JSON, các lần sau đọc lại. Khoá là sha256 của prompt, nên
sửa prompt là hỏng bản ghi cũ — đúng ý: prompt đổi thì câu trả lời phải ghi lại.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable

from reup.adapters.llm import LLMError


class CassetteMiss(LLMError):
    pass


def _key(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class CassetteLLM:
    def __init__(
        self,
        path: Path,
        recorder: Callable[[str, dict], dict] | None = None,
    ) -> None:
        self.path = Path(path)
        self.recorder = recorder

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def complete_json(self, prompt: str, schema: dict) -> dict:
        data = self._load()
        k = _key(prompt)
        if k in data:
            return data[k]["response"]

        if self.recorder is None:
            raise CassetteMiss(
                f"chưa có bản ghi cho prompt này trong {self.path}. "
                "Chạy lại với recorder để ghi, hoặc kiểm tra prompt có bị đổi không.\n"
                f"--- prompt ---\n{prompt[:500]}"
            )

        response = self.recorder(prompt, schema)
        data[k] = {"prompt": prompt, "response": response}
        self._save(data)
        return response
