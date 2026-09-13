"""Dịch tiêu đề video ở tab quét sang tiếng Việt, có cache ra đĩa.

Dịch sau khi trang đã hiện (JS gọi riêng) chứ không lúc quét: một lượt LLM mất
vài giây, bắt nút "Quét" chờ thêm chừng đó là phí. Cache theo đúng chuỗi gốc,
nên quét lại cùng kênh là hiện ngay, không gọi LLM lần nào.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CHUNK = 40

_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_VI = re.compile(
    r"[ăâđêôơưàáảãạèéẻẽẹìíỉĩịòóỏõọùúủũụỳýỷỹỵấầẩẫậắằẳẵặếềểễệốồổỗộớờởỡợứừửữự]",
    re.IGNORECASE,
)
_LETTER = re.compile(r"[A-Za-z]")


def needs_translation(text: str) -> bool:
    """Chữ Trung/Nhật/Hàn, hoặc chữ Latin không dấu Việt (thường là tiếng Anh)."""
    text = (text or "").strip()
    if not text:
        return False
    if _CJK.search(text):
        return True
    return bool(_LETTER.search(text)) and not _VI.search(text)


def _load(path: Path) -> dict[str, str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _prompt(batch: list[str]) -> str:
    lines = "\n".join(f"{i}. {t}" for i, t in enumerate(batch))
    return (
        "Dịch các tiêu đề video ngắn dưới đây sang tiếng Việt tự nhiên, ngắn gọn, "
        "giữ nguyên emoji. Bỏ hashtag ở cuối nếu có. Không thêm giải thích.\n"
        'Trả về JSON: {"items": [{"i": <số thứ tự>, "vi": "<bản dịch>"}]}\n\n'
        f"{lines}"
    )


SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"}, "vi": {"type": "string"}},
                "required": ["i", "vi"],
            },
        }
    },
    "required": ["items"],
}


def translate(titles: list[str], cache_path: Path, llm) -> tuple[dict[str, str], str]:
    """Trả (bản dịch theo chuỗi gốc, lỗi nếu có).

    Lỗi một lượt thì vẫn trả phần đã dịch được: thiếu vài bản dịch còn hơn
    trắng cả trang.
    """
    from reup.core.runner import atomic_write

    cache = _load(cache_path)
    wanted = list(dict.fromkeys(t for t in titles if needs_translation(t)))
    missing = [t for t in wanted if t not in cache]
    error = ""
    for start in range(0, len(missing), CHUNK):
        batch = missing[start:start + CHUNK]
        try:
            data = llm.complete_json(_prompt(batch), SCHEMA)
        except Exception as exc:  # mạng, model, JSON hỏng: báo chứ không ném
            error = str(exc)[:300]
            break
        for item in data.get("items") or []:
            try:
                idx, vi = int(item["i"]), str(item["vi"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= idx < len(batch) and vi:
                cache[batch[idx]] = vi
    if missing:
        atomic_write(Path(cache_path), json.dumps(cache, ensure_ascii=False, indent=1))
    return {t: cache[t] for t in wanted if t in cache}, error
