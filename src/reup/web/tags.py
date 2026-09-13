"""Tag gợi ý ở tab YouTube / TikTok / Douyin: đọc từ `tags.json` thay vì ghi cứng.

Lần đầu chưa có file thì chép từ `default_tags.json` đi kèm package. Người dùng
đặt tên tag bằng tiếng Việt; từ khoá tìm kiếm (`q`) do AI dịch sang tiếng Anh
hoặc Trung rồi mới lưu — hoặc gõ tay nếu muốn.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Callable

PLATFORMS = ("youtube", "tiktok", "douyin")
GROUPS = {"topic": "Chủ đề", "hashtag": "Hashtag"}
LANGS = {"en": "Tiếng Anh", "zh": "Tiếng Trung"}
DEFAULTS = Path(__file__).with_name("default_tags.json")

SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}


def default_lang(platform: str) -> str:
    # Tìm trên Douyin bằng tiếng Anh gần như không ra gì.
    return "zh" if platform == "douyin" else "en"


def _read(path: Path) -> list:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("tags") if isinstance(data, dict) and isinstance(data.get("tags"), list) else []


def load(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        from reup.core.runner import atomic_write

        atomic_write(path, DEFAULTS.read_text(encoding="utf-8"))
    out = []
    for t in _read(path):
        if not isinstance(t, dict) or t.get("platform") not in PLATFORMS or not t.get("q"):
            continue
        out.append({
            "id": str(t.get("id") or "t" + uuid.uuid4().hex[:8]),
            "platform": t["platform"],
            "group": t.get("group") if t.get("group") in GROUPS else "topic",
            "vi": str(t.get("vi") or t["q"]),
            "q": str(t["q"]),
            "lang": t.get("lang") if t.get("lang") in LANGS else default_lang(t["platform"]),
        })
    return out


def save(path: Path, tags: list[dict]) -> None:
    from reup.core.runner import atomic_write

    atomic_write(Path(path), json.dumps({"version": 1, "tags": tags}, ensure_ascii=False, indent=1) + "\n")


def for_platform(tags: list[dict], platform: str) -> list[dict]:
    return [t for t in tags if t["platform"] == platform]


def normalize(q: str, group: str) -> str:
    q = " ".join((q or "").split())[:120]
    if group == "hashtag":
        body = re.sub(r"[\s#]+", "", q)
        return "#" + body if body else ""
    return q


def clean(fields: dict) -> dict:
    platform = str(fields.get("platform") or "").strip()
    if platform not in PLATFORMS:
        raise ValueError(f"nền tảng phải là một trong: {', '.join(PLATFORMS)}")
    group = str(fields.get("group") or "topic").strip()
    if group not in GROUPS:
        raise ValueError("nhóm tag phải là Chủ đề hoặc Hashtag")
    vi = " ".join(str(fields.get("vi") or "").split())[:80]
    if not vi:
        raise ValueError("cần tên tiếng Việt cho tag")
    lang = str(fields.get("lang") or "").strip() or default_lang(platform)
    if lang not in LANGS:
        raise ValueError("ngôn ngữ dịch phải là tiếng Anh hoặc tiếng Trung")
    return {
        "platform": platform, "group": group, "vi": vi,
        "q": normalize(str(fields.get("q") or ""), group), "lang": lang,
    }


def _prompt(vi: str, lang: str, group: str) -> str:
    target = "tiếng Anh" if lang == "en" else "tiếng Trung giản thể"
    if group == "hashtag":
        shape = "một hashtag duy nhất, viết liền không dấu cách, bắt đầu bằng #"
    else:
        shape = "cụm từ khoá ngắn 1–4 từ, đúng kiểu người bản xứ gõ vào ô tìm video ngắn"
    return (
        f"Chuyển chủ đề tiếng Việt dưới đây thành {shape}, bằng {target}. "
        "Không giải thích.\n"
        'Trả về JSON: {"q": "<từ khoá>"}\n\n'
        f"Chủ đề: {vi}"
    )


def suggest(vi: str, lang: str, group: str, llm: Callable[[], object]) -> str:
    """Tên tiếng Việt → từ khoá tìm kiếm bằng AI. Lỗi gì cũng ném ValueError có lời."""
    tag = clean({"platform": "youtube", "group": group, "vi": vi, "lang": lang or "en"})
    try:
        data = llm().complete_json(_prompt(tag["vi"], tag["lang"], tag["group"]), SCHEMA)
        q = normalize(str((data or {}).get("q") or ""), tag["group"])
    except Exception as exc:  # dựng LLM, mạng, JSON hỏng
        raise ValueError(f"AI dịch lỗi: {str(exc)[:300]} — điền từ khoá tay rồi lưu.") from exc
    if not q:
        raise ValueError("AI trả từ khoá rỗng — điền từ khoá tay rồi lưu.")
    return q


def _finish(fields: dict, llm: Callable[[], object] | None) -> dict:
    tag = clean(fields)
    if not tag["q"]:
        if llm is None:
            raise ValueError("chưa có từ khoá")
        tag["q"] = suggest(tag["vi"], tag["lang"], tag["group"], llm)
    return tag


def _check_duplicate(tags: list[dict], tag: dict, skip_id: str = "") -> None:
    for t in tags:
        if t["id"] != skip_id and (t["platform"], t["group"], t["q"]) == (
            tag["platform"], tag["group"], tag["q"]
        ):
            raise ValueError(f"đã có tag “{t['vi']}” với từ khoá {t['q']}")


def create(path: Path, fields: dict, llm: Callable[[], object] | None = None) -> dict:
    tags = load(path)
    tag = _finish(fields, llm)
    _check_duplicate(tags, tag)
    tag = {"id": "t" + uuid.uuid4().hex[:8], **tag}
    save(path, tags + [tag])
    return tag


def update(path: Path, tag_id: str, fields: dict, llm: Callable[[], object] | None = None) -> dict:
    """Từ khoá để trống thì AI dịch lại từ tên tiếng Việt mới."""
    tags = load(path)
    idx = next((i for i, t in enumerate(tags) if t["id"] == tag_id), None)
    if idx is None:
        raise ValueError(f"không có tag {tag_id!r}")
    tag = _finish(fields, llm)
    _check_duplicate(tags, tag, skip_id=tag_id)
    tags[idx] = {"id": tag_id, **tag}
    save(path, tags)
    return tags[idx]


def delete(path: Path, tag_id: str) -> None:
    tags = load(path)
    kept = [t for t in tags if t["id"] != tag_id]
    if len(kept) == len(tags):
        raise ValueError(f"không có tag {tag_id!r}")
    save(path, kept)
