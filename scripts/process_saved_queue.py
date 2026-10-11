#!/usr/bin/env python3
"""Chạy xử lý toàn bộ video trong hàng chờ (saved_videos)."""
from __future__ import annotations

import json
from pathlib import Path

from reup.adapters.douyin_export import save_direct
from reup.config import load_config
from reup.core.job import create_job
from reup.core.runner import run_jobs
from reup.core.store import Store
from reup.dotenv import load_dotenv
from reup.stages import stages_for

JOBS_DIR = Path("jobs")
DB_PATH = Path("reup.db")
CONFIG_PATH = Path("config.toml")


def main() -> int:
    load_dotenv(Path(".env"))
    cfg = load_config(CONFIG_PATH)
    store = Store(DB_PATH)
    store.init_schema()

    saved = store.saved_videos()
    if not saved:
        print("Hàng chờ trống (không có video nào trong saved_videos).")
        return 0

    print(f"🎬 Tìm thấy {len(saved)} video trong hàng chờ. Bắt đầu tạo jobs...")
    created_jobs = []

    for item in saved:
        platform = item["platform"]
        video_id = item["video_id"]
        url = item.get("url", f"https://www.douyin.com/video/{video_id}")
        lang = "zh" if platform == "douyin" else "auto"
        media_url = item.get("media_url") or url
        title = item.get("title") or ""

        job = create_job(JOBS_DIR, url, lang)
        save_direct(job.root, media_url, video_id=video_id, url=url, title=title)
        store.upsert_job(job.id, job.source_url, "pending")
        store.mark_seen(platform, video_id)
        store.unsave_video(platform, video_id)
        created_jobs.append(job)
        print(f"  + Tạo job {job.id} cho video [{video_id}] {title[:40]}...")

    print(f"\n🚀 Bắt đầu chạy xử lý {len(created_jobs)} jobs...")
    stages = stages_for(cfg)
    workers = cfg.profile.concurrency or 1

    results = run_jobs(created_jobs, cfg, DB_PATH, stages, workers)

    print("\n" + "=" * 60)
    print("KẾT QUẢ XỬ LÝ HÀNG CHỜ:")
    for jid, status in results.items():
        print(f"  - Job {jid}: {status.upper()}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    main()
