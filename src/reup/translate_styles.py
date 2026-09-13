"""Luật dịch theo thể loại video — hàm thuần, không gọi LLM.

Một bộ luật chung cho mọi video (xưng hô, tiểu từ, thành ngữ kiểu Việt) cộng
một bộ luật riêng cho từng thể loại. Hài tình huống cần câu bật, drama cần
cảm xúc, hướng dẫn cần rõ từng bước — dùng chung một prompt thì cái nào cũng dở.

Thứ tự chọn thể loại: overrides.json của job > `[translate] genre` trong
config > LLM tự nhận ra lúc phân tích bối cảnh > đoán từ metadata nguồn.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Genre:
    key: str
    label: str
    rules: str


GENRES: dict[str, Genre] = {
    g.key: g
    for g in [
        Genre(
            "hai_tinh_huong",
            "Phim ngắn hài / tình huống",
            "- Giọng đời thường, lanh lợi như phim hài mạng Việt; câu bật, có nhịp.\n"
            "- Câu thoại chốt (punchline) phải giữ đủ ý để người xem hiểu vì sao "
            "buồn cười — ưu tiên số âm tiết cho câu chốt.\n"
            "- Chơi chữ, thành ngữ Trung đổi sang thành ngữ/câu cửa miệng Việt "
            "tương đương (vd '跑得了和尚跑不了庙' → 'chạy trời không khỏi nắng').\n"
            "- Cảm thán tự nhiên: 'ơ kìa', 'trời đất', 'thôi xong', 'hả', 'ủa'.\n"
            "- Kẻ lừa đảo nói ngọt, người bị lừa ngơ ngác rồi cuống — giọng phải "
            "thể hiện được vai.",
        ),
        Genre(
            "drama_tinh_cam",
            "Drama gia đình / tình cảm",
            "- Giữ cảm xúc: giận, tủi, day dứt phải nghe ra trong câu.\n"
            "- Xưng hô thể hiện quan hệ và thay đổi theo cảm xúc (vợ chồng cãi nhau "
            "'anh/cô' → 'tôi/cô'; mẹ con 'mẹ/con').\n"
            "- Câu trọn ý, không cụt lủn; dùng 'mà', 'đấy', 'cơ mà', 'sao lại' để có hồn.\n"
            "- Tránh từ Hán Việt sách vở trong lời thoại đời thường.",
        ),
        Genre(
            "review_ban_hang",
            "Review / đập hộp / bán hàng",
            "- Giọng người review Việt: hào hứng, gần gũi, xưng 'mình' gọi 'mọi người/các bạn'.\n"
            "- Giữ nguyên tên sản phẩm, thông số, con số, giá (đổi đơn vị tiền nếu cần "
            "thì ghi 'tệ').\n"
            "- Nhấn điểm mạnh: 'xịn', 'đỉnh', 'đáng tiền', 'bất ngờ là'.",
        ),
        Genre(
            "huong_dan",
            "Hướng dẫn / mẹo / nấu ăn",
            "- Rõ ràng, dễ làm theo: động từ đứng đầu ('cho', 'trộn', 'bấm', 'đợi').\n"
            "- Giữ đủ nguyên liệu, số lượng, thời gian, thứ tự bước — KHÔNG được bỏ "
            "chi tiết này dù phải bỏ chữ đệm.\n"
            "- Dùng tên gọi quen ở Việt Nam (vd '生抽' → 'xì dầu', '料酒' → 'rượu nấu ăn').\n"
            "- Xưng 'mình' gọi 'mọi người'; nối bước bằng 'rồi', 'tiếp theo', 'xong thì'.",
        ),
        Genre(
            "kien_thuc_ke_chuyen",
            "Kiến thức / kể chuyện / tin tức",
            "- Giọng người dẫn chuyện cuốn hút, mạch rõ: nguyên nhân → diễn biến → kết quả.\n"
            "- Tên người, địa danh Trung Quốc dùng âm Hán Việt quen thuộc "
            "(vd '北京' → 'Bắc Kinh', '李明' → 'Lý Minh').\n"
            "- Giữ con số, mốc thời gian; thuật ngữ dùng từ phổ thông Việt.\n"
            "- Mở câu gây tò mò được thì cứ dùng: 'bạn có biết', 'điều bất ngờ là'.",
        ),
        Genre(
            "vlog_doi_song",
            "Vlog / đời sống",
            "- Giọng tâm sự, thoải mái như đang nói chuyện với bạn bè.\n"
            "- Xưng 'mình', gọi 'mọi người'; dùng 'nè', 'nha', 'luôn á', 'ghê'.\n"
            "- Tên món, địa điểm giữ lại nếu người xem cần biết.",
        ),
        Genre(
            "dong_vat_cute",
            "Thú cưng / em bé / dễ thương",
            "- Giọng tinh nghịch, dễ thương; lời 'thoại' của con vật/em bé có thể "
            "xưng 'bé/em/tui'.\n"
            "- Dùng từ láy, cảm thán vui: 'ui chu choa', 'xinh xỉu', 'ngơ ngác'.",
        ),
        Genre(
            "chung",
            "Chung",
            "- Văn nói tự nhiên, đúng vai người nói, không có gì đặc biệt cần ưu tiên.",
        ),
    ]
}

DEFAULT_GENRE = "chung"
AUTO = "auto"


BASE_RULES = (
    "LUẬT CHUNG ĐỂ NGƯỜI VIỆT NGHE THẤY TỰ NHIÊN:\n"
    "- Dịch Ý, không dịch chữ. Hỏi: người Việt ở tình huống này sẽ nói câu gì?\n"
    "- Xưng hô theo quan hệ và tuổi (anh/chị/em, chú/cháu, bác/cháu, ông/tôi, "
    "mày/tao khi cãi nhau) — bám bảng nhân vật ở trên, giữ nhất quán suốt video. "
    "Tránh 'bạn/tôi' vô hồn trong lời thoại.\n"
    "- Dùng tiểu từ cuối câu cho có sắc thái: à, ạ, nhé, nha, đấy, chứ, hả, cơ, mà.\n"
    "- Câu phải tự hiểu được khi chỉ nghe, không nhìn chữ gốc: đủ chủ ngữ hoặc "
    "đối tượng khi thiếu nó sẽ khó hiểu (vd '看一下' trả lời cho 'xe kêu' → "
    "'Anh xem giúp em nhé', không phải 'Xem').\n"
    "- TẬN DỤNG TRẦN: nhắm khoảng 70–100% số âm tiết cho phép. Câu quá cụt "
    "(dưới một nửa trần) làm video khó hiểu và giọng đọc bị hẫng.\n"
    "- Câu gốc lặp lại do ASR/OCR bắt trùng: dịch thành câu nối tiếp hoặc phản "
    "ứng hợp lý, không lặp y nguyên chữ.\n"
)


# Từ khoá trong title/description/tags/categories của nguồn → thể loại.
# Chỉ là phương án cuối khi LLM không nhận ra; so khớp chữ thường.
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("hai_tinh_huong", ("喜剧", "搞笑", "幽默", "段子", "comedy", "funny", "prank", "骗局")),
    ("drama_tinh_cam", ("情感", "爱情", "婆婆", "家庭", "drama", "love story", "短剧")),
    ("huong_dan", ("教程", "做法", "美食", "菜谱", "技巧", "妙招", "recipe", "tutorial", "how to")),
    ("review_ban_hang", ("测评", "开箱", "好物", "review", "unboxing")),
    ("dong_vat_cute", ("猫", "狗", "萌宠", "宝宝", "cat", "dog", "pet", "baby")),
    ("kien_thuc_ke_chuyen", ("科普", "历史", "知识", "新闻", "news", "history", "science")),
    ("vlog_doi_song", ("vlog", "日常", "生活")),
]


def guess_genre(info: dict) -> str:
    """Đoán thể loại từ source.info.json của yt-dlp. Không chắc thì `chung`."""
    hay = " ".join(
        [
            str(info.get("title") or ""),
            str(info.get("description") or ""),
            " ".join(info.get("tags") or []),
            " ".join(info.get("categories") or []),
        ]
    ).lower()
    best, best_hits = DEFAULT_GENRE, 0
    for key, words in _KEYWORDS:
        hits = sum(1 for w in words if w in hay)
        if hits > best_hits:
            best, best_hits = key, hits
    return best


def validate_genre(value: str, field: str) -> str:
    value = (value or "").strip() or AUTO
    if value != AUTO and value not in GENRES:
        raise ValueError(
            f"{field} = {value!r} không hợp lệ. Chọn 'auto' hoặc một trong {sorted(GENRES)}"
        )
    return value


def style_block(genre: str, context: str = "") -> str:
    """Khối luật chèn vào prompt dịch."""
    g = GENRES.get(genre, GENRES[DEFAULT_GENRE])
    parts = []
    if context:
        parts.append(f"BỐI CẢNH VIDEO:\n{context}\n")
    parts.append(BASE_RULES)
    parts.append(f"LUẬT RIÊNG CHO THỂ LOẠI '{g.label}':\n{g.rules}\n")
    return "\n".join(parts)
