"""Kênh Douyin đã lưu: đặt tên cho file xuất để lần sau quét lại khỏi nạp.

Mỗi lần nạp vẫn ghi một file vào `douyin-exports/`. Sổ `_channels.json` chỉ
ghi kênh nào được lưu và tên người dùng đặt; file dùng để quét luôn là bản nạp
MỚI NHẤT của kênh đó, nên nạp lại một kênh đã lưu là tự cập nhật.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from reup.adapters.douyin_export import parse_export

INDEX = "_channels.json"
# File cũ chỉ còn để nhớ kênh: play_url trong đó hết hạn sau vài giờ. Quá mốc
# này thì UI nhắc quét lại bằng skill /douyin-export thay vì dùng dữ liệu cũ.
STALE_HOURS = 6


def _index_path(folder: Path) -> Path:
    return Path(folder) / INDEX


def _load(folder: Path) -> dict[str, dict]:
    try:
        data = json.loads(_index_path(folder).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(folder: Path, data: dict) -> None:
    from reup.core.runner import atomic_write

    atomic_write(_index_path(folder), json.dumps(data, ensure_ascii=False, indent=2))


def channel_id(raw: dict, path: Path) -> str:
    return str(raw.get("sec_user_id") or "").strip() or Path(path).stem


def exports(folder: Path) -> list[dict]:
    """Mỗi kênh một dòng — bản nạp mới nhất — mới nhất trước. File hỏng bỏ qua."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = sorted(
        (p for p in folder.glob("*.json") if p.name != INDEX),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    now = datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            count = len(parse_export(raw))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        uid = channel_id(raw, path)
        if uid in out:
            continue
        author = str(raw.get("author") or "").strip() or uid[-10:]
        exported = _exported_at(raw, path)
        hours = max(0.0, (now - exported).total_seconds() / 3600)
        out[uid] = {
            "uid": uid, "path": str(path.resolve()), "author": author,
            "count": count, "date": exported.astimezone().strftime("%Y-%m-%d"),
            "age": _age_label(hours), "stale": hours >= STALE_HOURS,
            "url": f"https://www.douyin.com/user/{uid}",
        }
    return list(out.values())


def store_export(folder: Path, body: bytes, raw: dict) -> Path:
    """Ghi một bản xuất: douyin_<uid24>_<giờ>.json. Không ghi đè bản cũ."""
    uid = re.sub(r"[^A-Za-z0-9_-]", "", str(raw.get("sec_user_id") or ""))[:24]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(folder) / f"douyin_{uid or 'kenh'}_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    return out


def _exported_at(raw: dict, path: Path) -> datetime:
    try:
        ts = datetime.fromisoformat(str(raw.get("exported_at") or "").replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.astimezone()
    except ValueError:
        return datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc)


def _age_label(hours: float) -> str:
    if hours < 1:
        return "vừa xong"
    if hours < 24:
        return f"{int(hours)} giờ trước"
    return f"{int(hours // 24)} ngày trước"


def saved_channels(folder: Path) -> list[dict]:
    """Kênh đã lưu, sắp theo tên. Kênh mất hết file thì không hiện."""
    index = _load(folder)
    rows = []
    for ex in exports(folder):
        entry = index.get(ex["uid"])
        if entry is not None:
            rows.append({**ex, "name": entry.get("name") or ex["author"]})
    return sorted(rows, key=lambda r: r["name"].lower())


def recent_unsaved(folder: Path, limit: int = 8) -> list[dict]:
    index = _load(folder)
    return [ex for ex in exports(folder) if ex["uid"] not in index][:limit]


def save_channel(folder: Path, uid: str, name: str = "") -> dict:
    """Lưu hoặc đổi tên kênh. Tên trống thì dùng tên tác giả trong file."""
    known = {ex["uid"]: ex for ex in exports(folder)}
    if uid not in known:
        raise ValueError(f"không có file xuất nào của kênh {uid!r}")
    name = " ".join((name or "").split())[:80] or known[uid]["author"]
    index = _load(folder)
    index[uid] = {
        "name": name,
        "saved_at": index.get(uid, {}).get("saved_at")
        or datetime.now().isoformat(timespec="seconds"),
    }
    _write(folder, index)
    return {**known[uid], "name": name}


def remove_channel(folder: Path, uid: str) -> None:
    """Bỏ lưu. Không xoá file xuất: kênh quay về danh sách nạp gần đây."""
    index = _load(folder)
    if index.pop(uid, None) is not None:
        _write(folder, index)
