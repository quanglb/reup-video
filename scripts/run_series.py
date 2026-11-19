#!/usr/bin/env python3
"""Tải N video chưa làm của một kênh Douyin về thư mục riêng rồi làm lần lượt.

Tải song song (link CDN chết sau vài giờ nên tải hết trước); job chạy tuần tự.
Thành phẩm vào output/<ngày>/<series>, nhật ký trùng lặp ở series.json.

  .venv/bin/python scripts/run_series.py --uid <sec_uid> --series ao_thuat \
      --name "Ảo thuật - Tony Wang" --count 20
"""
from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from reup.adapters.douyin_export import download, save_direct  # noqa: E402
from reup.config import load_config  # noqa: E402
from reup.core.job import create_job, load_job  # noqa: E402
from reup.core.runner import run_job  # noqa: E402
from reup.core.store import Store  # noqa: E402
from reup.dotenv import load_dotenv  # noqa: E402
from reup.stages import stages_for  # noqa: E402
from scripts.series_manager import load_series, register_video  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def latest_export(uid: str) -> dict:
    files = sorted(glob.glob(str(ROOT / f"douyin-exports/douyin_{uid[:24]}_*.json")))
    return json.loads(Path(files[-1]).read_text(encoding="utf-8"))


def pick(uid: str, series: str, count: int) -> list[dict]:
    raw = latest_export(uid)
    done = {k for k, v in load_series().get("series", {}).get(series, {}).get("videos", {}).items()
             if v.get("status") == "done"}
    ex = ROOT / "douyin-exports"
    for name in ("_done_log.json", "_excluded.json"):
        try:
            done |= set(json.loads((ex / name).read_text(encoding="utf-8")).get(uid, {}))
        except (OSError, ValueError):
            pass
    todo = [v for v in raw["videos"] if str(v["aweme_id"]) not in done and v.get("play_url", "").startswith("https://v")]
    todo.sort(key=lambda v: -int(v.get("digg_count") or 0))
    return todo[:count]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", required=True)
    ap.add_argument("--series", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--workers", type=int, default=3, help="số luồng tải")
    ap.add_argument("--jobs", type=int, default=3, help="số job chạy cùng lúc")
    a = ap.parse_args()

    load_dotenv(ROOT / ".env")
    cfg = load_config(ROOT / "config.toml")
    cfg = dataclasses.replace(
        cfg, review=dataclasses.replace(cfg.review, output_dir=f"output/%Y-%m-%d/{a.series}")
    )
    store = Store(ROOT / "reup.db")
    store.init_schema()
    stages = stages_for(cfg)
    dl_dir = ROOT / "downloads" / a.series
    dl_dir.mkdir(parents=True, exist_ok=True)

    manifest = dl_dir / "manifest.json"  # giữ nguyên danh sách 20 video qua các lần chạy lại
    if manifest.exists():
        want = json.loads(manifest.read_text(encoding="utf-8"))
        by_id = {str(v["aweme_id"]): v for v in latest_export(a.uid)["videos"]}
        videos = [by_id[i] for i in want if i in by_id]
    else:
        videos = pick(a.uid, a.series, a.count)
        manifest.write_text(json.dumps([str(v["aweme_id"]) for v in videos]), encoding="utf-8")
    log(f"Chọn {len(videos)} video (nhiều like nhất, chưa làm). Tải về {dl_dir}")
    for v in videos:
        register_video(a.series, str(v["aweme_id"]), original_title=v["desc"],
                       url=f"https://www.douyin.com/video/{v['aweme_id']}", status="queued",
                       series_meta={"name": a.name or a.series})

    import fcntl
    import threading

    resolve_lock = threading.Lock()

    def fetch(v: dict) -> Path:
        dest = dl_dir / f"{v['aweme_id']}.mp4"
        if not dest.exists():
            try:
                download(v["play_url"], dest)
            except Exception as e:
                log(f"  [resolve] link cũ hết hạn ({e}), đang lấy link mới qua Chrome cho #{v.get('position', '')} {v['aweme_id']}...")
                from reup.adapters.douyin_export import resolve_cdn_url
                for attempt in range(2):
                    try:
                        with resolve_lock:
                            fresh_url = resolve_cdn_url(str(v["aweme_id"]))
                        download(fresh_url, dest)
                        break
                    except Exception:
                        if attempt == 1:
                            raise
        log(f"  ⬇ tải xong #{v['position']} {dest.stat().st_size / 1e6:.1f}MB")
        return dest

    pool = ThreadPoolExecutor(max_workers=a.workers)
    futures = [pool.submit(fetch, v) for v in videos]

    reg_lock = threading.Lock()

    def reg(*args, **kw) -> None:
        with reg_lock:
            register_video(*args, **kw)

    def existing_job(vid: str) -> str | None:
        """Job chưa xong của video này (từ lần chạy trước) để làm tiếp, khỏi tạo trùng."""
        s = Store(ROOT / "reup.db")
        try:
            rows = [j for j in s.list_jobs() if f"/video/{vid}" in j["url"]]
        finally:
            s.close()
        return (rows[0]["id"], rows[0]["status"]) if rows else None

    def work(i: int, v: dict, fut) -> bool:
        vid, url, title = str(v["aweme_id"]), f"https://www.douyin.com/video/{v['aweme_id']}", v["desc"]
        try:
            src = fut.result()
        except Exception as e:  # noqa: BLE001
            log(f"[{i}] ❌ tải hỏng: {e}")
            reg(a.series, vid, original_title=title, url=url, status="failed")
            return False
        st = Store(ROOT / "reup.db")
        try:
            old = existing_job(vid)
            if old:
                if old[1] == "done":
                    log(f"[{i}/{len(videos)}] {vid} đã xong trước đó, bỏ qua")
                    return True
                job = load_job(ROOT / "jobs", old[0])
                if not job.source_video.exists():
                    job.source_video.write_bytes(src.read_bytes())
                log(f"[{i}/{len(videos)}] {vid} làm tiếp job {old[0]}")
            else:
                job = create_job(ROOT / "jobs", url, "zh")
                save_direct(job.root, v["play_url"], video_id=vid, url=url, title=title,
                            uploader=v.get("author", ""), duration_ms=int(v.get("duration_ms") or 0),
                            like_count=int(v.get("digg_count") or 0), share_count=int(v.get("share_count") or 0))
                job.source_video.write_bytes(src.read_bytes())
                job.source_info.write_text(json.dumps({
                    "id": vid, "title": title, "description": title, "uploader": v.get("author", ""),
                    "duration": int(v.get("duration_ms") or 0) / 1000, "like_count": v.get("digg_count", 0),
                    "repost_count": v.get("share_count", 0), "webpage_url": url, "extractor": "douyin"},
                    ensure_ascii=False, indent=2), encoding="utf-8")
                st.upsert_job(job.id, job.source_url, "pending")
                log(f"[{i}/{len(videos)}] {vid} {title[:40]} → job {job.id}")
            st.mark_seen("douyin", vid)
            reg(a.series, vid, original_title=title, url=url, status="running")
            try:
                status = run_job(job, cfg, st, stages)
            except Exception as e:  # noqa: BLE001
                log(f"  ❌ job {job.id}: {e}")
                reg(a.series, vid, original_title=title, url=url, status="failed")
                return False
            out = vi = ""
            if status == "done":
                try:
                    out = json.loads(job.exported_json.read_text(encoding="utf-8")).get("output", "")
                    vi = json.loads(job.meta_json.read_text(encoding="utf-8")).get("title", "")
                except Exception:  # noqa: BLE001
                    pass
                for p in [job.source_video, *job.root.rglob("*.wav")]:
                    p.unlink(missing_ok=True)
                import shutil
                shutil.rmtree(job.root / "frames", ignore_errors=True)
            reg(a.series, vid, title=vi, original_title=title, url=url, status=status, output_file=out)
            log(f"  → [{i}] {status.upper()} {vi}")
            return status == "done"
        finally:
            st.close()

    lock = open(dl_dir / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("Đã có một tiến trình run_series khác đang chạy cho series này — thoát để khỏi làm trùng.")
        return 1
    with ThreadPoolExecutor(max_workers=a.jobs) as jobs_pool:
        results = list(jobs_pool.map(lambda t: work(*t), [(i, v, f) for i, (v, f) in enumerate(zip(videos, futures), 1)]))
    ok = sum(results)
    log(f"XONG: {ok}/{len(videos)} video làm xong")
    return 0


if __name__ == "__main__":
    sys.exit(main())
