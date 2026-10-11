"""Kiểm nguồn ngay sau khi tải — chỉ luật, không tốn token.

Chặn sớm video chắc chắn vi phạm hoặc chất lượng quá thấp, trước khi tốn
Demucs/Whisper/TTS/render. Nguồn có dấu hiệu nhạy cảm nhưng còn cứu được (vd.
ảo thuật gắn `#赌神`) thì chỉ ghi cờ: bước dịch và bước viết tiêu đề đọc cờ này để
dùng bộ từ chặt hơn, và bước `policy` cuối cùng bắt buộc gọi AI.
"""
from __future__ import annotations

import json

from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.media.ffmpeg import probe
from reup.policy import PolicyBlocked
from reup.policy import rules


def source_text(job: Job) -> tuple[str, str]:
    """(tiêu đề, mô tả) của nguồn — tiêu đề Douyin thường dồn luôn hashtag."""
    if not job.source_info.exists():
        return "", ""
    info = json.loads(job.source_info.read_text(encoding="utf-8"))
    title = str(info.get("title") or "")
    desc = str(info.get("description") or "")
    return title, "" if desc == title else desc


def evaluate(title: str, desc: str, duration_s: float, short_side: int, cfg: Config) -> dict:
    """Hàm thuần: kết quả kiểm nguồn. `blocked` kèm lý do khi phải dừng."""
    p = cfg.policy
    hits = rules.scan_text(title, "tiêu đề gốc") + rules.scan_text(desc, "mô tả gốc")
    reasons: list[str] = []
    warnings: list[str] = []
    for h in hits:
        (reasons if h.severity == rules.BLOCK else warnings).append(h.label())
    if duration_s and duration_s < p.block_duration_s:
        reasons.append(f"video quá ngắn ({duration_s:.1f}s) — TikTok xếp vào chất lượng thấp")
    elif duration_s and duration_s < p.min_duration_s:
        warnings.append(f"video ngắn ({duration_s:.1f}s)")
    if short_side and short_side < p.block_short_side:
        reasons.append(f"độ phân giải quá thấp (cạnh ngắn {short_side}px) — chất lượng thấp")
    elif short_side and short_side < p.min_short_side:
        warnings.append(f"độ phân giải thấp (cạnh ngắn {short_side}px)")
    return {
        "blocked": bool(reasons),
        "reasons": reasons,
        "warnings": warnings,
        "risk_categories": rules.categories(hits),
        "hits": [h.to_dict() for h in hits],
        "duration_s": round(duration_s, 2),
        "short_side": short_side,
    }


def load(job: Job) -> dict:
    """Kết quả precheck đã ghi, hoặc rỗng (job cũ chưa có stage này)."""
    if not job.precheck_json.exists():
        return {}
    return json.loads(job.precheck_json.read_text(encoding="utf-8"))


def risk_categories(job: Job) -> list[str]:
    return list(load(job).get("risk_categories") or [])


def run(job: Job, cfg: Config) -> None:
    title, desc = source_text(job)
    info = probe(job.source_video)
    result = evaluate(
        title, desc, info.duration_ms / 1000, min(info.width, info.height) or 0, cfg
    )
    if not cfg.policy.enabled:
        result["blocked"] = False
    atomic_write(job.precheck_json, json.dumps(result, ensure_ascii=False, indent=2))
    if result["blocked"] and not job.policy_allowed:
        # Xoá kết quả để lần chạy sau (sau `reup policy-allow`) kiểm lại.
        job.precheck_json.unlink()
        raise PolicyBlocked(
            "Nguồn vi phạm chính sách, dừng trước khi tốn công xử lý: "
            + "; ".join(result["reasons"])
            + f". Vẫn muốn làm: `reup policy-allow {job.id}` rồi chạy lại."
        )


SPEC = StageSpec(name="precheck", produces=("precheck.json",), run=run)
