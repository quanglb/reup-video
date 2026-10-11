"""Một lượt AI vision duy nhất cho mỗi video: hình + tiêu đề + nội dung.

Giữ token thấp vì chạy nhiều video mỗi ngày:
- một ảnh ghép nhỏ thay vì nhiều ảnh, `detail: low`;
- không gửi cả transcript: tóm tắt có sẵn từ bước dịch + một đoạn trích ngắn +
  đúng những câu bị luật bắt;
- kết quả kiểm cục bộ đi kèm để AI khỏi tự dò lại;
- câu trả lời là JSON mã lỗi ngắn, không văn xuôi.
"""
from __future__ import annotations

from reup.policy import rules

PROMPT_VERSION = "v2"
EXCERPT_CHARS = 600

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "severity": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["code", "severity"],
            },
        },
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "issues"],
}

# Mã lỗi: chữ cái đầu cho biết sửa được bằng chữ hay không.
ISSUE_LABELS = {
    "V1": "ô mờ / ô che / logo / watermark / tên kênh khác / mã QR trên hình",
    "V2": "chữ nước ngoài (Hán…) còn sót trên hình",
    "V3": "chất lượng hình thấp (nhoè, vỡ, ảnh tĩnh, viền đen quá nhiều)",
    "V4": "outro / màn kết thúc / kêu gọi đăng ký của kênh gốc",
    "C1": "cờ bạc, cá cược, dạy gian lận",
    "C2": "nguy hiểm, bạo lực thật, vũ khí",
    "C3": "tình dục, khoả thân, gợi dục",
    "C4": "ma tuý, rượu bia, thuốc lá",
    "C5": "sai lệch y tế, lừa đảo, hứa kiếm tiền",
    "C6": "thù ghét, quấy rối, xúc phạm",
    "T1": "tiêu đề giật tít sai sự thật",
    "T2": "tiêu đề/mô tả có từ nhạy cảm",
    "T3": "hashtag không liên quan / spam",
}
TEXT_CODES = {"T1", "T2", "T3"}


def build_prompt(
    meta: dict,
    summary: str,
    excerpt: str,
    flagged_lines: list[str],
    local_notes: list[str],
    frames_in_sheet: int,
    project_note: str = "",
) -> str:
    codes = "\n".join(f"{k} {v}" for k, v in ISSUE_LABELS.items())
    parts = [
        "Bạn là bộ lọc kiểm duyệt TikTok cho video ngắn tiếng Việt (bản lồng tiếng/biên "
        "tập lại từ video nước ngoài). Xét theo Nguyên tắc Cộng đồng TikTok và tiêu chí "
        "được đề xuất (For You: nội dung nguyên tác, chất lượng, không spam).",
        f"Ảnh: lưới {frames_in_sheet} khung rải đều của thành phẩm, đánh số trái→phải, "
        "trên→dưới. Phụ đề và chữ tiếng Việt trên hình là do kênh tự làm — hợp lệ.",
    ]
    if project_note:
        parts.append(project_note)
    parts += [
        f"TIÊU ĐỀ: {meta.get('title', '')}",
        f"MÔ TẢ: {meta.get('description', '')}",
        f"HASHTAG: {' '.join('#' + t for t in meta.get('hashtags') or [])}",
    ]
    if summary:
        parts.append(f"TÓM TẮT NỘI DUNG: {summary}")
    if excerpt:
        parts.append(f"TRÍCH LỜI THOẠI: {excerpt}")
    if flagged_lines:
        parts.append("CÂU BỊ LUẬT TỪ KHOÁ BẮT:\n" + "\n".join(f"- {s}" for s in flagged_lines[:6]))
    if local_notes:
        parts.append("KIỂM TỰ ĐỘNG TRÊN MÁY:\n" + "\n".join(f"- {s}" for s in local_notes))
    parts += [
        "MÃ LỖI:\n" + codes,
        "Tiền lệ: TikTok đã XOÁ một video ảo thuật bài vì gắn với cờ bạc (“Thần Bài”, "
        "“đánh bài ngày Tết”, dạy lén đổi bài “để khán giả không nhận ra”). Nội dung "
        "như vậy trong phụ đề/hình là C1 high; chỉ trong tiêu đề thì là T2.",
        "Trả JSON: {\"verdict\":\"pass|fix|reject\",\"issues\":[{\"code\":\"V1\","
        "\"severity\":\"low|high\",\"note\":\"<=12 từ, khung số mấy\"}],"
        "\"title\":\"\",\"description\":\"\",\"hashtags\":[]}",
        "- pass: không có lỗi high.",
        "- fix: chỉ có lỗi sửa được bằng chữ (T*, hoặc C* nhẹ do cách diễn đạt) → điền "
        "title/description/hashtags mới, an toàn, đúng nội dung, tiếng Việt.",
        "- reject: vi phạm Nguyên tắc Cộng đồng thật trong NỘI DUNG, hoặc lỗi hình "
        "V1/V2 rõ ràng.",
        "Chỉ nêu lỗi nhìn thấy thật, không suy diễn. Không có lỗi thì issues rỗng.",
    ]
    return "\n".join(parts)


def excerpt_of(lines: list[str], limit: int = EXCERPT_CHARS) -> str:
    text = " ".join(s.strip() for s in lines if s.strip())
    return text[:limit] + ("…" if len(text) > limit else "")


def normalize(answer: dict) -> dict:
    """Ép câu trả lời về dạng chắc chắn dùng được."""
    verdict = str(answer.get("verdict") or "").strip().lower()
    if verdict not in ("pass", "fix", "reject"):
        verdict = "fix" if answer.get("title") else "pass"
    issues = []
    for it in answer.get("issues") or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "").strip().upper()[:3]
        if code not in ISSUE_LABELS:
            continue
        sev = "high" if str(it.get("severity") or "").lower().startswith("h") else "low"
        issues.append({"code": code, "severity": sev, "note": str(it.get("note") or "")[:120]})
    return {
        "verdict": verdict,
        "issues": issues,
        "title": str(answer.get("title") or "").strip(),
        "description": str(answer.get("description") or "").strip(),
        "hashtags": [str(t) for t in answer.get("hashtags") or []],
    }


def is_safe_title(title: str) -> bool:
    return bool(title) and not rules.has_cjk(title) and not rules.scan_text(title)
