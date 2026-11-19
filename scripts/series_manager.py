#!/usr/bin/env python3
"""Quản lý series và kiểm tra trùng lặp video ID."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

SERIES_FILE = Path("series.json")


def load_series(path: Path = SERIES_FILE) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"series": {}}


def save_series(data: dict, path: Path = SERIES_FILE) -> None:
    temp_path = path.with_suffix(".tmp.json")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def check_video(video_id: str, series_id: str | None = None) -> dict | None:
    """Kiểm tra xem video_id đã tồn tại trong series hay chưa.
    
    Trả về dict thông tin nếu trùng, None nếu chưa có.
    """
    data = load_series()
    for s_id, s_info in data.get("series", {}).items():
        if series_id and s_id != series_id:
            continue
        videos = s_info.get("videos", {})
        if str(video_id) in videos:
            res = dict(videos[str(video_id)])
            res["series_id"] = s_id
            res["series_name"] = s_info.get("name", s_id)
            return res
    return None


def register_video(
    series_id: str,
    video_id: str,
    title: str = "",
    original_title: str = "",
    url: str = "",
    status: str = "queued",
    output_file: str = "",
    series_meta: dict | None = None,
) -> None:
    """Ghi nhận hoặc cập nhật thông tin video vào series."""
    data = load_series()
    if "series" not in data:
        data["series"] = {}
    if series_id not in data["series"]:
        data["series"][series_id] = {
            "id": series_id,
            "name": series_id,
            "created_at": datetime.now().isoformat(),
            "videos": {},
        }
    if series_meta:
        for k, v in series_meta.items():
            if k != "videos":
                data["series"][series_id][k] = v

    now = datetime.now().isoformat()
    videos = data["series"][series_id].setdefault("videos", {})
    existing = videos.get(str(video_id), {})
    cur_status = existing.get("status")
    final_status = "done" if (cur_status == "done" and status == "queued") else status
    videos[str(video_id)] = {
        "video_id": str(video_id),
        "title": title or existing.get("title", ""),
        "original_title": original_title or existing.get("original_title", ""),
        "url": url or existing.get("url", f"https://www.douyin.com/video/{video_id}"),
        "status": final_status,
        "output_file": output_file or existing.get("output_file", ""),
        "updated_at": now,
        "created_at": existing.get("created_at", now),
    }
    save_series(data)


def main() -> int:
    if len(sys.argv) < 2:
        print("Sử dụng:")
        print("  python scripts/series_manager.py check <video_id>")
        print("  python scripts/series_manager.py list [series_id]")
        return 0

    cmd = sys.argv[1]
    if cmd == "check":
        if len(sys.argv) < 3:
            print("Vui lòng nhập video_id cần kiểm tra.")
            return 1
        vid = sys.argv[2]
        found = check_video(vid)
        if found:
            print(f"⚠️ [TRÙNG LẶP] Video ID {vid} ĐÃ TỒN TẠI trong series '{found['series_name']}'!")
            print(f"  - Trạng thái: {found.get('status')}")
            print(f"  - Tiêu đề Việt: {found.get('title')}")
            print(f"  - Tiêu đề gốc: {found.get('original_title')}")
            print(f"  - File thành phẩm: {found.get('output_file') or '(chưa xuất)'}")
            print(f"  - Cập nhật lúc: {found.get('updated_at')}")
            return 2
        else:
            print(f"✅ Video ID {vid} chưa từng có trong bất kỳ series nào.")
            return 0

    elif cmd == "list":
        s_id = sys.argv[2] if len(sys.argv) > 2 else None
        data = load_series()
        for sid, sinfo in data.get("series", {}).items():
            if s_id and sid != s_id:
                continue
            vids = sinfo.get("videos", {})
            print(f"\n📂 Series: {sinfo.get('name', sid)} ({len(vids)} video)")
            print(f"   Thư mục xuất: {sinfo.get('output_dir', '')}")
            for i, (v_id, item) in enumerate(vids.items(), 1):
                status_icon = "✅" if item.get("status") == "done" else "⏳"
                print(f"   {i:02d}. {status_icon} [{item.get('status')}] {v_id} - {item.get('title') or item.get('original_title')}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
