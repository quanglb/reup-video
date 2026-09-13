"""Stage 13 — sinh tiêu đề / mô tả / hashtag và chép video ra thư mục output.

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
from reup.models import Transcript

META_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description", "hashtags"],
}

MAX_TITLE_CHARS = 100
MAX_HASHTAGS = 8


def build_prompt(transcript: Transcript, source_title: str) -> str:
    body = " ".join(s.text for s in transcript.segments)[:1500]
    return (
        "Viết phần mô tả cho một video ngắn tiếng Việt sắp đăng lên "
        "YouTube Shorts và TikTok.\n\n"
        f"Tiêu đề bản gốc: {source_title or '(không có)'}\n"
        f"Lời thoại tiếng Việt: {body}\n\n"
        "Yêu cầu:\n"
        f"- title: tiêu đề tiếng Việt, tối đa {MAX_TITLE_CHARS} ký tự, "
        "gợi tò mò nhưng không giật gân sai sự thật.\n"
        "- description: 2-3 câu tiếng Việt.\n"
        f"- hashtags: tối đa {MAX_HASHTAGS} thẻ, không kèm dấu #, "
        "không dấu cách, chữ thường.\n\n"
        'Trả JSON: {"title": "...", "description": "...", "hashtags": ["..."]}'
    )


def clean_hashtags(tags: list) -> list[str]:
    out = []
    for tag in tags or []:
        clean = str(tag).lstrip("#").strip().replace(" ", "").lower()
        if clean and clean not in out:
            out.append(clean)
    return out[:MAX_HASHTAGS]


def run_with(job: Job, cfg: Config, llm: LLMAdapter) -> None:
    if not job.final_mp4.exists():
        raise FileNotFoundError(f"chưa có {job.final_mp4} — stage compose phải chạy trước")

    transcript = Transcript.load(job.translation_json)
    source_title = ""
    if job.source_info.exists():
        source_title = json.loads(
            job.source_info.read_text(encoding="utf-8")
        ).get("title", "")

    answer = llm.complete_json(build_prompt(transcript, source_title), META_SCHEMA)
    meta = {
        "title": (answer.get("title") or "").strip()[:MAX_TITLE_CHARS],
        "description": (answer.get("description") or "").strip(),
        "hashtags": clean_hashtags(answer.get("hashtags")),
        "source_url": job.source_url,
        "source_title": source_title,
    }
    atomic_write(job.meta_json, json.dumps(meta, ensure_ascii=False, indent=2))

    import os

    out_dir = cfg.output_dir_path()
    out_dir.mkdir(parents=True, exist_ok=True)
    target_mp4 = out_dir / f"{job.id}.mp4"
    tmp_target = out_dir / f"{job.id}.tmp.mp4"
    shutil.copy2(job.final_mp4, tmp_target)
    os.replace(tmp_target, target_mp4)
    atomic_write(
        out_dir / f"{job.id}.json", json.dumps(meta, ensure_ascii=False, indent=2)
    )


def run(job: Job, cfg: Config) -> None:
    from reup.adapters.registry import make_llm

    run_with(job, cfg, make_llm(cfg, "export"))


SPEC = StageSpec(name="export", produces=("meta.json",), run=run)
