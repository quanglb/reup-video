#!/usr/bin/env python3
"""Chạy xử lý toàn bộ video trong hàng chờ (saved_videos), kiểm tra trùng lặp và ghi nhận vào series."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from reup.adapters.douyin_export import save_direct
from reup.config import load_config
from reup.core.job import create_job
from reup.core.runner import run_job
from reup.core.store import Store
from reup.dotenv import load_dotenv
from reup.stages import stages_for
import sys

# Đảm bảo import được cả thư mục scripts và root
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from scripts.series_manager import check_video, register_video
except ImportError:
    from series_manager import check_video, register_video

JOBS_DIR = Path("jobs")
DB_PATH = Path("reup.db")
CONFIG_PATH = Path("config.toml")
SERIES_ID = "nau_an"


def cleanup_intermediate(job_root: Path) -> None:
    """Xóa bớt file wav trung gian sau khi đã xuất thành phẩm để tiết kiệm đĩa."""
    audio_dir = job_root / "audio"
    if audio_dir.exists():
        for p in audio_dir.glob("*.wav"):
            try:
                p.unlink()
            except OSError:
                pass
        demucs_dir = audio_dir / "demucs"
        if demucs_dir.exists():
            shutil.rmtree(demucs_dir, ignore_errors=True)


def main() -> int:
    load_dotenv(Path(".env"))
    cfg = load_config(CONFIG_PATH)
    store = Store(DB_PATH)
    store.init_schema()

    saved = store.saved_videos()
    if not saved:
        print("Hàng chờ trống (không có video nào trong saved_videos).")
        return 0

    print(f"🎬 Tìm thấy {len(saved)} video trong hàng chờ.")
    print(f"📁 Thư mục xuất thành phẩm hiện tại: {cfg.output_dir_path()}\n")

    stages = stages_for(cfg)
    total = len(saved)

    for idx, item in enumerate(saved, 1):
        platform = item["platform"]
        video_id = str(item["video_id"])
        url = item.get("url", f"https://www.douyin.com/video/{video_id}")
        lang = "zh" if platform == "douyin" else "auto"
        media_url = item.get("media_url") or url
        orig_title = item.get("title") or ""

        # Kiểm tra trùng lặp ID trước khi làm
        dup = check_video(video_id, series_id=SERIES_ID)
        if dup and dup.get("status") == "done":
            print(f"[{idx}/{total}] ⚠️ BỎ QUA DO TRÙNG: Video ID {video_id} đã hoàn thành trong series '{dup.get('series_name')}'!")
            print(f"    File: {dup.get('output_file')}")
            store.unsave_video(platform, video_id)
            continue

        print(f"\n{'='*70}")
        print(f"[{idx}/{total}] 🚀 Bắt đầu xử lý: [{video_id}] {orig_title[:50]}...")
        print(f"{'='*70}")

        job = create_job(JOBS_DIR, url, lang)
        save_direct(job.root, media_url, video_id=video_id, url=url, title=orig_title)
        store.upsert_job(job.id, job.source_url, "pending")
        store.mark_seen(platform, video_id)
        store.unsave_video(platform, video_id)

        register_video(
            series_id=SERIES_ID,
            video_id=video_id,
            original_title=orig_title,
            url=url,
            status="running",
        )

        try:
            status = run_job(job, cfg, store, stages)
            print(f"  -> Trạng thái kết thúc: {status.upper()}")

            if status == "done":
                # Đọc output file và title tiếng Việt từ job
                out_file = ""
                vi_title = ""
                if job.exported_json.exists():
                    try:
                        exp = json.loads(job.exported_json.read_text(encoding="utf-8"))
                        out_file = exp.get("output", "")
                    except Exception:
                        pass
                if job.meta_json.exists():
                    try:
                        meta = json.loads(job.meta_json.read_text(encoding="utf-8"))
                        vi_title = meta.get("title", "")
                    except Exception:
                        pass

                register_video(
                    series_id=SERIES_ID,
                    video_id=video_id,
                    title=vi_title,
                    original_title=orig_title,
                    url=url,
                    status="done",
                    output_file=out_file,
                )
                cleanup_intermediate(job.root)
                print(f"  -> ✅ Đã lưu vào series {SERIES_ID}: {vi_title}")
                print(f"  -> File xuất: {out_file}")
            else:
                register_video(
                    series_id=SERIES_ID,
                    video_id=video_id,
                    original_title=orig_title,
                    url=url,
                    status=status,
                )
        except Exception as e:
            print(f"  -> ❌ Lỗi xử lý job {job.id}: {e}")
            register_video(
                series_id=SERIES_ID,
                video_id=video_id,
                original_title=orig_title,
                url=url,
                status="failed",
            )

    print("\n" + "=" * 70)
    print("HOÀN TẤT XỬ LÝ TOÀN BỘ HÀNG CHỜ!")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    main()
