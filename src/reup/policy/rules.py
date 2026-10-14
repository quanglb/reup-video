"""Luật chữ tất định: từ khoá nhạy cảm, từ thay an toàn, lọc hashtag.

Hàm thuần, không gọi mạng — chạy được ở mọi stage có chữ mà không tốn token.

Hai mức:
- `block`: gần như chắc chắn bị gỡ (quảng bá cờ bạc, ma tuý, tình dục, tự hại).
  Thấy ở nguồn thì dừng job ngay, thấy ở thành phẩm thì chặn không xuất.
- `risky`: chưa chắc vi phạm nhưng đã gây ra lần xoá video "Thần Bài" — cần viết
  lại hoặc để AI xét ngữ cảnh.

Không đưa từ bạo lực của game ("giết", "chém", "hạ gục") vào đây: video game nói
thế suốt và TikTok không phạt chuyện đó. AI xét ngữ cảnh bạo lực thật.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

BLOCK = "block"
RISKY = "risky"

# Nhãn tiếng Việt cho từng nhóm — dùng trong lời báo và prompt.
CATEGORY_LABELS: dict[str, str] = {
    "gambling": "cờ bạc / cá cược",
    "gambling_theme": "chủ đề bài bạc",
    "cheating": "gian lận / lừa đảo",
    "drugs": "ma tuý / chất cấm",
    "sexual": "tình dục",
    "self_harm": "tự hại",
    "weapons": "vũ khí / chất nổ",
    "medical": "y tế sai lệch",
    "cross_platform": "nhắc nền tảng khác",
}

# (nhóm, mức, các từ). Chữ Việt so theo ranh giới từ; chữ Hán so chuỗi con.
_TERMS: list[tuple[str, str, tuple[str, ...]]] = [
    ("gambling", BLOCK, (
        "cá cược", "cá độ", "nhà cái", "nổ hũ", "tài xỉu", "lô đề", "xóc đĩa",
        "đổi thưởng", "game bài đổi thưởng", "casino", "betting", "gambling",
        "博彩", "赌博", "赌场", "棋牌", "彩票", "下注", "押注", "百家乐", "老虎机",
    )),
    ("drugs", BLOCK, (
        "ma túy", "ma tuý", "cần sa", "thuốc lắc", "heroin", "cocaine",
        "毒品", "大麻", "冰毒", "摇头丸",
    )),
    ("sexual", BLOCK, (
        "khiêu dâm", "sex", "phim người lớn", "色情", "裸体", "约炮",
    )),
    ("self_harm", BLOCK, (
        "tự tử", "tự sát", "自杀", "自残",
    )),
    ("gambling_theme", RISKY, (
        "thần bài", "đánh bài", "đánh bạc", "cờ bạc", "bài bạc", "sòng bạc",
        "chiếu bạc", "ăn tiền", "tráo bài", "bịp", "poker",
        "赌神", "赌圣", "赌", "千术", "出千", "老千", "验牌",
    )),
    ("cheating", RISKY, (
        "gian lận", "ăn gian", "lừa đảo", "出老千", "作弊",
    )),
    ("weapons", RISKY, (
        "súng", "thuốc nổ", "chất nổ", "dao găm", "枪支", "炸药",
    )),
    ("medical", RISKY, (
        "thần dược", "chữa khỏi", "trị dứt điểm", "神药",
    )),
    ("cross_platform", RISKY, (
        "douyin", "抖音", "kuaishou", "快手", "bilibili", "youtube shorts",
        "reels", "instagram",
    )),
]

# Từ thay an toàn, áp tất định vào phụ đề/giọng đọc/tiêu đề. Chỉ những cặp giữ
# nguyên nghĩa câu; từ không có cách nói trung tính thì để AI viết lại cả câu.
SAFE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("thần bài", "cao thủ ảo thuật"),
    ("đánh bài", "chơi bài"),
    ("bài bạc", "bài"),
    ("tráo bài", "đổi bài"),
    ("bịp", "lừa mắt"),
)

# Hashtag của nền tảng khác: đăng TikTok mà gắn #shorts #reels là dấu hiệu
# chép nguyên từ nơi khác.
CROSS_PLATFORM_TAGS = frozenset({
    "shorts", "short", "youtubeshorts", "ytshorts", "youtube", "reels", "reel",
    "instagram", "ig", "facebook", "fb", "capcut", "douyin", "kuaishou",
    "bilibili", "tiktok",
})
# Thẻ chung chung không liên quan nội dung: TikTok coi là spam hashtag.
GENERIC_TAGS = frozenset({
    "fyp", "fypシ", "fypage", "foryou", "foryoupage", "viral", "xuhuong",
    "trending", "trend", "xh", "tiktokvn", "viralvideo", "fy",
})
MAX_HASHTAGS = 5

# Tiêu đề giống tiêu đề cũ tới mức này (so theo TỪ) thì coi là lặp khuôn.
# Đo trên 15 tiêu đề đã xuất: các cặp cùng khuôn ra 0.5-0.6, hai tiêu đề khác
# chủ đề ra dưới 0.3.
SIMILAR_TITLE_RATIO = 0.5

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


@dataclass(frozen=True)
class Hit:
    category: str
    term: str
    severity: str
    where: str = ""

    def label(self) -> str:
        cat = CATEGORY_LABELS.get(self.category, self.category)
        where = f" ({self.where})" if self.where else ""
        return f"“{self.term}” — {cat}{where}"

    def to_dict(self) -> dict:
        return {"category": self.category, "term": self.term,
                "severity": self.severity, "where": self.where}


def norm(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").lower()


def fold(text: str) -> str:
    """Bỏ dấu, bỏ khoảng trắng, chữ thường — để so hashtag `thanbai` với `thần bài`."""
    out = unicodedata.normalize("NFD", norm(text)).replace("đ", "d")
    out = "".join(ch for ch in out if unicodedata.category(ch) != "Mn")
    return re.sub(r"[\s_\-#]+", "", out)


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def _pattern(term: str) -> re.Pattern:
    t = norm(term)
    if has_cjk(t):
        return re.compile(re.escape(t))
    # \w của Python nhận cả chữ có dấu tiếng Việt, nên ranh giới từ đúng cả với "bịp".
    return re.compile(rf"(?<!\w){re.escape(t)}(?!\w)")


_COMPILED: list[tuple[str, str, str, re.Pattern]] = [
    (cat, sev, term, _pattern(term)) for cat, sev, terms in _TERMS for term in terms
]
_FOLDED: list[tuple[str, str, str, str]] = [
    (cat, sev, term, fold(term))
    for cat, sev, terms in _TERMS for term in terms
    if not has_cjk(term) and len(fold(term)) >= 5
]


def scan_text(text: str, where: str = "") -> list[Hit]:
    """Mọi từ nhạy cảm trong một đoạn chữ. Mỗi (nhóm, từ) báo một lần."""
    t = norm(text)
    if not t.strip():
        return []
    hits: list[Hit] = []
    seen: set[tuple[str, str]] = set()
    for cat, sev, term, pat in _COMPILED:
        if (cat, term) in seen:
            continue
        if pat.search(t):
            # "赌" đứng một mình là lưới vét — đã khớp từ dài hơn chứa nó thì thôi.
            if term == "赌" and any(h.category == cat and "赌" in h.term for h in hits):
                continue
            seen.add((cat, term))
            hits.append(Hit(cat, term, sev, where))
    return hits


def scan_hashtag(tag: str, where: str = "hashtag") -> list[Hit]:
    """Hashtag viết liền không dấu (`thanbai`) nên so bản đã gập dấu."""
    f = fold(tag)
    hits = scan_text(tag, where)
    for cat, sev, term, ft in _FOLDED:
        if ft in f and not any(h.term == term for h in hits):
            hits.append(Hit(cat, term, sev, where))
    return hits


def worst(hits: list[Hit]) -> str | None:
    if any(h.severity == BLOCK for h in hits):
        return BLOCK
    return RISKY if hits else None


def categories(hits: list[Hit]) -> list[str]:
    out: list[str] = []
    for h in hits:
        if h.category not in out:
            out.append(h.category)
    return out


def _match_case(src: str, repl: str) -> str:
    if src.isupper() and len(src) > 1:
        return repl.upper()
    if src[:1].isupper():
        if all(w[:1].isupper() for w in src.split() if w):
            return " ".join(w[:1].upper() + w[1:] for w in repl.split())
        return repl[:1].upper() + repl[1:]
    return repl


def soften(text: str) -> str:
    """Áp từ thay an toàn, giữ kiểu viết hoa của chữ gốc."""
    if not text:
        return text
    out = unicodedata.normalize("NFC", text)
    for src, repl in SAFE_REPLACEMENTS:
        pat = re.compile(rf"(?<!\w){re.escape(src)}(?!\w)", re.IGNORECASE)
        out = pat.sub(lambda m, r=repl: _match_case(m.group(0), r), out)
    return out


def clean_hashtags(tags, limit: int = MAX_HASHTAGS) -> list[str]:
    """Bỏ thẻ nền tảng khác, thẻ chung chung, thẻ dính từ nhạy cảm; tối đa `limit`."""
    out: list[str] = []
    for tag in tags or []:
        clean = str(tag).lstrip("#").strip().replace(" ", "").lower()
        if not clean or clean in out:
            continue
        if clean in CROSS_PLATFORM_TAGS or clean in GENERIC_TAGS:
            continue
        if scan_hashtag(clean):
            continue
        out.append(clean)
    return out[:limit]


def strip_cross_platform(text: str) -> str:
    """Bỏ "#shorts", "#reels"… lọt vào tiêu đề/mô tả."""
    def drop(m: re.Match) -> str:
        tag = m.group(1).lower()
        return "" if tag in CROSS_PLATFORM_TAGS or tag in GENERIC_TAGS else m.group(0)

    out = re.sub(r"#(\w+)", drop, text or "")
    return " ".join(out.split())


def most_similar(title: str, others: list[str]) -> tuple[float, str]:
    """(tỉ lệ giống cao nhất, tiêu đề đó) so với danh sách tiêu đề cũ.

    So theo dãy từ chứ không theo ký tự: tiêu đề cùng khuôn ("Đẳng cấp X: Những
    pha xử lý Y") chung nhiều cụm từ, còn so ký tự thì cặp nào cũng ra ~0.4.
    """
    a = _words(title)
    best, best_title = 0.0, ""
    if not a:
        return best, best_title
    for other in others:
        b = _words(other)
        if not b:
            continue
        r = difflib.SequenceMatcher(None, a, b).ratio()
        if r > best:
            best, best_title = r, other
    return best, best_title


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", norm(text))


def vocab_guide(extra_categories: list[str] | None = None) -> str:
    """Khối luật ngắn (~100 token) chèn vào prompt dịch và prompt tiêu đề."""
    pairs = "; ".join(f"“{s}” → “{r}”" for s, r in SAFE_REPLACEMENTS)
    lines = [
        "LUẬT AN TOÀN NỀN TẢNG (TikTok rất gắt — vi phạm là bị xoá video):",
        f"- Thay từ: {pairs}.",
        "- Không dùng từ về cờ bạc, cá cược, ăn tiền, gian lận, ma tuý, tình dục, "
        "tự hại, vũ khí. Ảo thuật bài chỉ là trình diễn/giải trí, không gắn với "
        "đánh bạc hay lừa người khác.",
        "- Không nhắc nền tảng khác (Douyin, YouTube, Reels, Shorts).",
        "- Không giật tít sai sự thật, không hứa hẹn chữa bệnh/kiếm tiền.",
    ]
    if extra_categories:
        cats = ", ".join(CATEGORY_LABELS.get(c, c) for c in extra_categories)
        lines.append(
            f"- Video gốc có dấu hiệu nhạy cảm ({cats}): diễn đạt lại trung tính, "
            "giữ ý chính nhưng bỏ mọi liên tưởng tới các chủ đề đó."
        )
    return "\n".join(lines)
