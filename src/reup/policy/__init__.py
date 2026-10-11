"""Chống dính chính sách nền tảng (TikTok trước tiên) — xem docs/tiktok-policy-plan.md.

- `rules`: luật chữ tất định, miễn phí — chạy ở mọi chỗ có chữ.
- `local`: kiểm thành phẩm bằng Apple Vision trên máy (chữ gốc sót, mã QR).
- `review`: một lượt AI vision duy nhất cho mỗi video.
"""
from __future__ import annotations


class PolicyBlocked(RuntimeError):
    """Video vi phạm chính sách: dừng job, không xuất ra output/.

    Người dùng vẫn ép đăng được bằng `reup policy-allow <job_id>`.
    """
