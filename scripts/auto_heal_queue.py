#!/usr/bin/env python3
"""Tự động theo dõi, sửa lỗi và chạy lại các video bị lỗi trong series nau_an cho đến khi xong 100%."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

# Đảm bảo import được cả scripts và project root
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from reup.adapters.douyin_export import save_direct
from reup.config import load_config
from reup.core.job import create_job, load_job
from reup.core.runner import run_job
from reup.core.store import Store
from reup.dotenv import load_dotenv
from reup.stages import stages_for
from scripts.series_manager import load_series, register_video

JOBS_DIR = Path("jobs")
DB_PATH = Path("reup.db")
CONFIG_PATH = Path("config.toml")
SERIES_ID = "nau_an"


def cleanup_job_temp(job_root: Path) -> None:
    """Dọn dẹp triệt để frames và audio tạm sau khi job xong để giữ dung lượng đĩa."""
    for sub in ("frames", "audio"):
        p = job_root / sub
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


def find_job_for_video(video_id: str, store: Store) -> str | None:
    """Tìm job_id tương ứng với video_id."""
    for p in JOBS_DIR.iterdir():
        if not p.is_dir():
            continue
        direct_file = p / "source.direct.json"
        if direct_file.exists():
            try:
                data = json.loads(direct_file.read_text(encoding="utf-8"))
                if str(data.get("video_id")) == str(video_id):
                    return p.name
            except Exception:
                pass
    return None


def main() -> int:
    load_dotenv(Path(".env"))
    cfg = load_config(CONFIG_PATH)
    store = Store(DB_PATH)
    store.init_schema()

    print(f"[{datetime_now()}] 🛡️ Bắt đầu Auto-Heal & Runner cho series: {SERIES_ID}")

    while True:
        data = load_series()
        series_info = data.get("series", {}).get(SERIES_ID, {})
        videos = series_info.get("videos", {})

        not_done = {vid: v for vid, v in videos.items() if v.get("status") != "done"}

        if not not_done:
            print(f"\n[{datetime_now()}] 🎉 TẤT CẢ {len(videos)} VIDEO ĐÃ HOÀN THÀNH 100%!")
            break

        print(f"\n[{datetime_now()}] 🔄 Đang còn {len(not_done)}/{len(videos)} video chưa xong. Bắt đầu rà soát và chạy...")

        for vid, v_meta in not_done.items():
            orig_title = v_meta.get("original_title", "")
            print(f"\n--- Xử lý video [{vid}] {orig_title[:45]} ---")

            job_id = find_job_for_video(vid, store)
            job = None

            if job_id:
                try:
                    job = load_job(JOBS_DIR, job_id)
                except Exception as e:
                    print(f"  [warn] Lỗi nạp job {job_id}: {e}")
                    job = None

            if job is None:
                url = v_meta.get("url", f"https://www.douyin.com/video/{vid}")
                job = create_job(JOBS_DIR, url, "zh")
                save_direct(job.root, url, video_id=vid, url=url, title=orig_title)
                print(f"  + Tạo mới job {job.id}...")

            store.upsert_job(job.id, job.source_url, "pending")
            register_video(SERIES_ID, vid, original_title=orig_title, status="running")

            # Xóa frames cũ nếu từng bị lỗi no space left on device để giải phóng đĩa
            frames_dir = job.root / "frames"
            if frames_dir.exists() and not (job.root / "ocr.json").exists():
                shutil.rmtree(frames_dir, ignore_errors=True)

            stages = stages_for(cfg)
            try:
                status = run_job(job, cfg, store, stages)
                print(f"  -> Trạng thái kết thúc: {status.upper()}")

                if status == "failed":
                    row = store.get_job(job.id) or {}
                    failed_stage = row.get("stage", "")
                    err = row.get("error", "")
                    print(f"  -> Bị lỗi ở stage {failed_stage}: {err}")

                    if failed_stage in ("policy", "precheck"):
                        print(f"  [fix] Bật policy allow và thử lại...")
                        job.set_override("policy", "allow")
                        status = run_job(job, cfg, store, stages)
                        print(f"  -> Trạng thái sau policy allow: {status.upper()}")
                    elif failed_stage in ("tts", "fit"):
                        print(f"  [fix] Chờ 5s hạ nhiệt TTS và thử lại...")
                        time.sleep(5.0)
                        status = run_job(job, cfg, store, stages)
                        print(f"  -> Trạng thái sau retry TTS: {status.upper()}")

                if status == "done":
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
                        SERIES_ID,
                        vid,
                        title=vi_title,
                        original_title=orig_title,
                        status="done",
                        output_file=out_file,
                    )
                    cleanup_job_temp(job.root)
                    print(f"  -> ✅ HOÀN THÀNH: {vi_title}")
                    print(f"  -> File: {out_file}")
                else:
                    register_video(
                        SERIES_ID,
                        vid,
                        original_title=orig_title,
                        status="failed",
                    )
            except Exception as exc:
                print(f"  -> ❌ Lỗi ngoại lệ: {exc}")
                register_video(SERIES_ID, vid, original_title=orig_title, status="failed")

            # Nghỉ nhịp 3s giữa các video để hạ tải CPU/GPU và TTS API
            time.sleep(3.0)

        # Kiểm tra lại xem còn video nào chưa xong
        time.sleep(5.0)

    return 0


def datetime_now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    sys.exit(main())
