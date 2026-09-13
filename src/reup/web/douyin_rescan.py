"""Nút Quét lại ở tab Douyin: xuất file kênh mới trong luồng nền.

Một lần chỉ quét một kênh — dùng chung một Chrome, và Douyin dễ chặn khi gọi dồn.
Trạng thái chỉ giữ trong bộ nhớ: kết quả thật là file mới trong `douyin-exports/`.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from reup.web import douyin_channels


class Rescanner:
    def __init__(self, folder: Path, export: Callable[..., dict] | None = None):
        self.folder = Path(folder)
        self._export = export
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def status(self, uid: str) -> dict:
        with self._lock:
            return dict(self._jobs.get(uid) or {"status": "idle"})

    def start(self, uid: str) -> dict:
        with self._lock:
            busy = next((u for u, j in self._jobs.items() if j["status"] == "running"), None)
            if busy == uid:
                return dict(self._jobs[uid])
            if busy:
                raise RuntimeError("đang quét lại một kênh khác, chờ xong rồi bấm tiếp.")
            self._jobs[uid] = {"status": "running", "videos": 0, "error": "", "path": ""}
        threading.Thread(target=self._run, args=(uid,), daemon=True).start()
        return self.status(uid)

    def _update(self, uid: str, **fields) -> None:
        with self._lock:
            self._jobs[uid].update(fields)

    def _run(self, uid: str) -> None:
        export = self._export
        if export is None:
            from reup.adapters.douyin_chrome import export_channel as export
        try:
            raw = export(uid, on_progress=lambda n: self._update(uid, videos=n))
            body = json.dumps(raw, ensure_ascii=False).encode("utf-8")
            path = douyin_channels.store_export(self.folder, body, raw)
            self._update(uid, status="done", videos=len(raw.get("videos") or []), path=str(path))
        except Exception as exc:  # mọi lỗi đều phải hiện lên nút, không chết lặng trong luồng
            self._update(uid, status="error", error=str(exc) or type(exc).__name__)
