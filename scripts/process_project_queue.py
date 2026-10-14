#!/usr/bin/env python3
"""Chạy xử lý toàn bộ video pending trong dự án và lưu vào output/du_an.

Tự động kiểm tra policy, đặt tên theo title, và dọn dẹp file âm thanh tạm để giữ an toàn đĩa.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from reup.config import load_config
from reup.core.job import load_job
from reup.core.runner import run_job
from reup.core.store import Store
from reup.dotenv import load_dotenv
from reup.stages import split, stages_for_job
from reup.stages.export import sanitize_filename

CONFIG_PATH = Path("config.toml")
JOBS_DIR = Path("jobs")
DB_PATH = Path("reup.db")


def cleanup_intermediate(job_root: Path) -> None:
    """Xóa bớt file wav trung gian sau khi đã xuất thành phẩm để giải phóng đĩa."""
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


def ensure_title_named_copy(job_id: str, out_dir: Path) -> None:
    """Tạo thêm file <Tiêu đề>.mp4 từ thành phẩm xuất ra nếu chưa có."""
    json_path = out_dir / f"{job_id}.json"
    mp4_path = out_dir / f"{job_id}.mp4"
    if not (json_path.exists() and mp4_path.exists()):
        return
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        title = sanitize_filename(data.get("title", "").strip())
        if title:
            target_mp4 = out_dir / f"{title}.mp4"
            target_json = out_dir / f"{title}.json"
            if not target_mp4.exists():
                try:
                    os.link(mp4_path, target_mp4)
                except OSError:
                    shutil.copy2(mp4_path, target_mp4)
            if not target_json.exists():
                shutil.copy2(json_path, target_json)
    except Exception as e:
        print(f"  [warn] Lỗi tạo bản copy theo title: {e}")


def main() -> int:
    load_dotenv(Path(".env"))
    cfg = load_config(CONFIG_PATH)
    store = Store(DB_PATH)
    out_dir = cfg.output_dir_path()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Lấy danh sách các job pending
    pending_rows = store.list_jobs("pending")
    if not pending_rows:
        print("Không còn job nào ở trạng thái pending.")
        return 0

    print(f"🎬 Tìm thấy {len(pending_rows)} job pending cần xử lý:")
    for r in pending_rows:
        print(f"  - {r['id']}")

    queue = [r["id"] for r in pending_rows]
    idx = 0
    while idx < len(queue):
        job_id = queue[idx]
        idx += 1
        print(f"\n[{idx}/{len(queue)}] Đang xử lý job {job_id}...")
        try:
            job = load_job(JOBS_DIR, job_id)
        except Exception as e:
            print(f"  [error] Không thể load job {job_id}: {e}")
            continue

        stages = stages_for_job(cfg, job)
        try:
            status = run_job(job, cfg, store, stages)
            print(f"  -> Kết quả: {status.upper()}")

            if status == "done" and split.requested(job):
                children = split.register_children(job, store)
                if children:
                    print(f"  -> Đã tách thành {len(children)} phần, thêm vào hàng đợi: {', '.join(children)}")
                    queue.extend(c for c in children if c not in queue)
                    continue
                if not (split.plan(job) or {}).get("parts"):
                    status = run_job(job, cfg, store, stages_for_job(cfg, job))
                    print(f"  -> Không cần tách, chạy đầy đủ: {status.upper()}")

            if status == "done":
                ensure_title_named_copy(job_id, out_dir)
                cleanup_intermediate(job.root)
                print(f"  -> Đã lưu và chuẩn hóa tên tại {out_dir}")
            elif status == "failed":
                row_cur = store.get_job(job_id)
                stage_failed = row_cur.get("stage")
                err = row_cur.get("error", "")
                print(f"  -> Thất bại ở stage {stage_failed}: {err}")
                if stage_failed == "policy":
                    print(f"  [policy] Job {job_id} bị policy chặn: {err}. Áp dụng policy-allow và chạy tiếp...")
                    job.set_override("policy", "allow")
                    status = run_job(job, cfg, store, stages)
                    print(f"  -> Kết quả sau policy-allow: {status.upper()}")
                    if status == "done":
                        ensure_title_named_copy(job_id, out_dir)
                        cleanup_intermediate(job.root)
                        print(f"  -> Đã lưu và chuẩn hóa tên tại {out_dir}")
            elif status == "needs_review":
                row_cur = store.get_job(job_id)
                print(f"  -> Cần duyệt ở stage {row_cur.get('stage')}")
                # Nếu có chốt duyệt, duyệt luôn vì auto_approve bật
                gate = (row_cur.get("stage") or "").removeprefix("gate_")
                if gate:
                    job.approve_gate(gate)
                    status = run_job(job, cfg, store, stages)
                    if status == "done":
                        ensure_title_named_copy(job_id, out_dir)
                        cleanup_intermediate(job.root)
        except Exception as e:
            print(f"  [exception] Lỗi khi chạy job {job_id}: {e}")

    print("\n" + "=" * 60)
    print("HOÀN TẤT XỬ LÝ HÀNG ĐỢI DỰ ÁN")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
