"""Sổ ghi video Douyin nào của kênh nào đã làm xong.

Nguồn sự thật là bảng `jobs` (url → aweme_id) đối chiếu với file xuất của kênh.
Nhưng xoá job là mất dấu, nên mỗi lần đồng bộ ta ghi thêm vào `_done_log.json`
trong douyin-exports/ và KHÔNG BAO GIỜ bỏ dòng nào: video đã làm vẫn nhớ dù
job bị xoá.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

LOG = "_done_log.json"
_VIDEO = re.compile(r"douyin\.com/video/(\d+)")


def _read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def latest_exports(folder: Path) -> dict[str, dict]:
    """uid → nội dung file xuất mới nhất (theo mtime)."""
    out: dict[str, dict] = {}
    files = sorted(
        (p for p in Path(folder).glob("douyin_*.json")), key=lambda p: p.stat().st_mtime
    )
    for p in files:
        raw = _read(p)
        uid = str(raw.get("sec_user_id") or "").strip()
        if uid and isinstance(raw.get("videos"), list):
            out[uid] = raw
    return out


def load(folder: Path) -> dict[str, dict[str, dict]]:
    return _read(Path(folder) / LOG)


def sync(folder: Path, jobs: list[dict]) -> dict[str, dict[str, dict]]:
    """Ghi thêm mọi job Douyin đã xong vào sổ; trả về sổ sau khi gộp."""
    folder = Path(folder)
    log = load(folder)
    owner: dict[str, tuple[str, dict]] = {}
    for uid, raw in latest_exports(folder).items():
        for v in raw["videos"]:
            owner.setdefault(str(v.get("aweme_id")), (uid, v))
    changed = False
    for j in jobs:
        m = _VIDEO.search(j.get("url") or "")
        if not m or j.get("status") != "done" or m.group(1) not in owner:
            continue
        uid, v = owner[m.group(1)]
        entry = log.setdefault(uid, {})
        if m.group(1) in entry:
            continue
        entry[m.group(1)] = {
            "job_id": j["id"],
            "title": (v.get("desc") or "").strip(),
            "done_at": datetime.fromtimestamp(j.get("updated_at") or 0).isoformat(timespec="seconds"),
        }
        changed = True
    if changed:
        from reup.core.runner import atomic_write

        atomic_write(folder / LOG, json.dumps(log, ensure_ascii=False, indent=2))
    return log


def channel_rows(folder: Path, uid: str, jobs: list[dict], series: str = "") -> dict | None:
    """Mọi video của kênh kèm trạng thái: done / running / ready / downloading /
    failed / todo. `series` (nếu có) cho biết video nào đã xếp hàng và tải về
    downloads/<series>/ nhưng chưa thành job."""
    raw = latest_exports(folder).get(uid)
    if raw is None:
        return None
    log = sync(folder, jobs).get(uid, {})
    skip = _read(folder / "_excluded.json").get(uid, {})  # video lạc đề lẫn trong file xuất
    by_id: dict[str, dict] = {}
    for j in jobs:
        m = _VIDEO.search(j.get("url") or "")
        if m:
            by_id[m.group(1)] = j
    root = Path(folder).parent
    queued = _read(root / "series.json").get("series", {}).get(series, {}).get("videos", {}) if series else {}
    rows = []
    for v in raw["videos"]:
        vid = str(v.get("aweme_id"))
        if vid in skip:
            continue
        job = by_id.get(vid)
        logged = log.get(vid)
        if (job and job["status"] == "done") or logged:
            state = "done"
        elif job and job["status"] in ("failed", "error"):
            state = "failed"
        elif job:
            state = "running"
        elif queued.get(vid, {}).get("status") in ("queued", "running"):
            has_file = (root / "downloads" / series / f"{vid}.mp4").exists()
            state = "ready" if has_file else "downloading"
        else:
            state = "todo"
        rows.append({
            "id": vid, "title": (v.get("desc") or "").strip(), "likes": v.get("digg_count") or 0,
            "duration_ms": v.get("duration_ms") or 0, "cover": v.get("cover") or "",
            "state": state, "job_id": (job or logged or {}).get("job_id") or (job or {}).get("id"),
            "done_at": (logged or {}).get("done_at", ""),
            "has_job": bool(job), "job_status": (job or {}).get("status", ""),
            "job_stage": (job or {}).get("stage") or "", "job_error": (job or {}).get("error") or "",
            "url": f"https://www.douyin.com/video/{vid}",
        })
    counts = {k: sum(1 for r in rows if r["state"] == k) for k in ("done", "running", "ready", "downloading", "failed", "todo")}
    return {"uid": uid, "author": raw.get("author") or uid[-10:], "rows": rows, "counts": counts}
