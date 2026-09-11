"""Đọc `.env` vào biến môi trường. Stdlib, không thêm dependency.

Chỉ để khoá API không phải nằm trong config.toml (thứ có thể lỡ tay commit)
cũng không phải gõ lại mỗi phiên shell. `.env` đã nằm trong .gitignore.

Không ghi đè biến đã có sẵn trong môi trường: biến thật luôn thắng file, để
`GEMINI_API_KEY=... reup run` tạm thời vẫn dùng được.
"""
from __future__ import annotations

import os
from pathlib import Path


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def load_dotenv(path: Path | None = None) -> list[str]:
    """Nạp `.env` vào os.environ. Trả về tên các biến đã nạp (không phải giá trị)."""
    path = Path(path or ".env")
    if not path.exists():
        return []
    loaded = []
    for key, value in parse_env(path.read_text(encoding="utf-8")).items():
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
