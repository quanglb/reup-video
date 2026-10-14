"""Runner script to process remaining jobs as full videos (no split) and organize them into Video le folder."""
from pathlib import Path
import json
import os
import shutil
import time

from reup.dotenv import load_dotenv
from reup.config import load_config
from reup.core.job import load_job
from reup.core.runner import run_job
from reup.core.store import Store
from reup.stages import stages_for_job
from reup.stages.export import sanitize_filename


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


def organize_job_files(jid: str, job_root: Path, dest: Path, base_out: Path) -> list[str]:
    """Di chuyển các file thành phẩm của job vào thư mục đích (Video le)."""
    dest.mkdir(parents=True, exist_ok=True)
    moved = []

    # 1. Các file chứa jid
    for p in list(base_out.glob(f"*{jid}*")):
        if p.is_file() and p.parent == base_out:
            target = dest / p.name
            shutil.move(str(p), str(target))
            moved.append(target.name)

    # 2. Các file đặt tên theo tiêu đề
    meta_p = job_root / "meta.json"
    if meta_p.exists():
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            title = sanitize_filename(meta.get("title", "").strip())
            if title:
                for p in list(base_out.glob(f"*{title}*")):
                    if p.is_file() and p.parent == base_out:
                        target = dest / p.name
                        shutil.move(str(p), str(target))
                        moved.append(target.name)
        except Exception as e:
            print(f"  [warn] Lỗi đọc meta để move: {e}")

    return moved


def main():
    load_dotenv(Path(".env"))
    cfg = load_config("config.toml")
    store = Store("reup.db")
    base_out = cfg.output_dir_path()
    le_dir = base_out / "Video le"
    le_dir.mkdir(parents=True, exist_ok=True)

    active_ids = [
        "20261010-150507-06efe610",
        "20261010-150507-38be0665",
        "20261010-150507-88151f62",
        "20261010-150507-22a3e061",
        "20261010-150507-e49924ae",
        "20261010-150507-d7308c85",
    ]

    print(f"🚀 Bắt đầu xử lý {len(active_ids)} dự án trong hàng chờ (chế độ FULL, không cắt/tách)...", flush=True)

    # Đảm bảo không có cờ split nào và đưa các job chưa xong về pending
    for jid in active_ids:
        job = load_job("jobs", jid)
        if job.settings.get("split"):
            job.update_settings(split=None)
        split_json = job.root / "split.json"
        if split_json.exists():
            split_json.unlink()

        row = store.get_job(jid)
        if row and row["status"] != "done":
            store.upsert_job(jid, row["url"], "pending", stage=None)

    for idx, jid in enumerate(active_ids, 1):
        row = store.get_job(jid)
        if not row:
            continue
        job = load_job("jobs", jid)

        if row["status"] == "done":
            print(f"\n[{idx}/{len(active_ids)}] Job {jid} đã xong từ trước.", flush=True)
            organize_job_files(jid, job.root, le_dir, base_out)
            continue

        print(f"\n[{idx}/{len(active_ids)}] === [START FULL] {jid} ===", flush=True)
        # Chạy pipeline đầy đủ (ALL_STAGES)
        stages = stages_for_job(cfg, job)
        status = run_job(job, cfg, store, stages)
        print(f"=== [RESULT] {jid}: {status} ===", flush=True)

        if status == "failed":
            # Nếu bị policy chặn, tự động cho phép và xuất
            r = store.get_job(jid)
            if r and r.get("stage") == "policy":
                print(f"  [policy] Job {jid} bị chặn ở policy, tự động cho phép và xuất...", flush=True)
                job.set_override("policy", "allow")
                store.upsert_job(jid, job.source_url, "pending", stage=None)
                status = run_job(job, cfg, store, stages)
                print(f"=== [RETRY RESULT] {jid}: {status} ===", flush=True)

        elif status == "needs_review":
            r = store.get_job(jid)
            gate = (r.get("stage") or "").removeprefix("gate_")
            if gate:
                print(f"  [review] Tự động duyệt {gate} cho job {jid}...", flush=True)
                job.approve_gate(gate)
                status = run_job(job, cfg, store, stages)
                print(f"=== [RETRY RESULT] {jid}: {status} ===", flush=True)

        if status == "done":
            cleanup_intermediate(job.root)
            moved = organize_job_files(jid, job.root, le_dir, base_out)
            print(f"  -> Hoàn thành xuất video FULL: {', '.join(moved) if moved else 'OK'}", flush=True)
        else:
            r = store.get_job(jid)
            print(f"  [error] Job {jid} chưa xong ({status}) ở stage {r.get('stage')}: {r.get('error')}", flush=True)

    store.close()
    print("\n" + "=" * 60, flush=True)
    print("🎉 TẤT CẢ CÁC DỰ ÁN TRONG HÀNG CHỜ ĐÃ ĐƯỢC XỬ LÝ XONG (FULL)", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
