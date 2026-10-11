"""Stage cuối — chép thành phẩm và tiêu đề (đã qua `meta` và `policy`) ra output/.

Pipeline dừng ở file. Đăng bài làm thủ công (spec §2): hạn mức YouTube Data API
chỉ cho ~6 upload/ngày, và TikTok bắt duyệt app mới cho đăng công khai.
"""
from __future__ import annotations

import json
import shutil

from reup.adapters.llm import LLMAdapter
from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec

from reup.policy import rules
from reup.stages.meta import MAX_TITLE_CHARS, META_SCHEMA  # noqa: F401 — giữ chỗ gọi cũ

MAX_HASHTAGS = rules.MAX_HASHTAGS
# Khoá của meta.json được chép ra output/ — phần còn lại (headers, repaired) là
# việc nội bộ của pipeline, người đăng bài không cần thấy.
PUBLIC_KEYS = ("title", "description", "hashtags", "source_url", "source_title")


def clean_hashtags(tags: list) -> list[str]:
    return rules.clean_hashtags(tags)


def public_meta(meta: dict, policy: dict | None = None) -> dict:
    out = {k: meta.get(k, "" if k != "hashtags" else []) for k in PUBLIC_KEYS}
    tags = " ".join(f"#{t}" for t in out["hashtags"])
    # Dán thẳng vào ô mô tả TikTok: đúng thẻ đã lọc, không tự thêm #Shorts #Reels.
    out["caption"] = f"{out['title']}\n{out['description']}\n{tags}".strip()
    if policy:
        out["policy"] = {
            "verdict": policy.get("verdict"),
            "notes": policy.get("notes", [])[:5],
        }
    return out


def sanitize_filename(name: str) -> str:
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, " ")
    return " ".join(name.split()).strip()


def next_output_index(out_dir: Path) -> int:
    import re
    max_idx = 0
    if not out_dir.exists():
        return 1
    for p in out_dir.iterdir():
        m = re.match(r"^(\d+)\.", p.name)
        if m:
            try:
                idx = int(m.group(1))
                if idx > max_idx:
                    max_idx = idx
            except ValueError:
                pass
    return max_idx + 1


def run_with(job: Job, cfg: Config, llm: LLMAdapter | None = None) -> None:
    """Chép thành phẩm ra output/. Chưa có meta.json (gọi trực tiếp, job cũ) thì
    sinh luôn bằng `llm`."""
    if not job.final_mp4.exists():
        raise FileNotFoundError(f"chưa có {job.final_mp4} — stage compose phải chạy trước")
    if not job.meta_json.exists():
        if llm is None:
            raise FileNotFoundError(f"chưa có {job.meta_json} — stage meta phải chạy trước")
        from reup.stages import meta as meta_stage

        meta_stage.run_with(job, cfg, llm)
    meta = json.loads(job.meta_json.read_text(encoding="utf-8"))
    policy = (
        json.loads(job.policy_json.read_text(encoding="utf-8"))
        if job.policy_json.exists() else None
    )
    out_meta = public_meta(meta, policy)
    payload = json.dumps(out_meta, ensure_ascii=False, indent=2)

    import os
    import re

    out_dir = cfg.output_dir_path()
    out_dir.mkdir(parents=True, exist_ok=True)
    target_mp4 = out_dir / f"{job.id}.mp4"
    tmp_target = out_dir / f"{job.id}.tmp.mp4"
    shutil.copy2(job.final_mp4, tmp_target)
    os.replace(tmp_target, target_mp4)
    atomic_write(out_dir / f"{job.id}.json", payload)

    # Đặt tên file theo thứ tự + tiêu đề: "NN. <Tiêu đề>.mp4" và "NN. <Tiêu đề>.json"
    named_base = ""
    title = meta.get("title", "").strip()
    if title:
        safe_title = sanitize_filename(title)
        existing_titled = [
            p for p in out_dir.glob("*.json")
            if safe_title in p.name and re.match(r"^\d+\.", p.name)
        ]
        if existing_titled:
            named_base = existing_titled[0].stem
        else:
            idx = next_output_index(out_dir)
            named_base = f"{idx:02d}. {safe_title}"

        named_mp4 = out_dir / f"{named_base}.mp4"
        named_json = out_dir / f"{named_base}.json"
        if named_mp4.exists():
            named_mp4.unlink()  # thành phẩm có thể vừa render lại
        try:
            os.link(target_mp4, named_mp4)
        except OSError:
            shutil.copy2(target_mp4, named_mp4)
        atomic_write(named_json, payload)

    atomic_write(
        job.exported_json,
        json.dumps({"output": str(out_dir / f"{named_base or job.id}.mp4")}, ensure_ascii=False),
    )


def run(job: Job, cfg: Config) -> None:
    run_with(job, cfg)


SPEC = StageSpec(name="export", produces=("exported.json",), run=run)
