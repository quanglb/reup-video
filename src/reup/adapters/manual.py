"""Nguồn thủ công: người dùng dán link, yt-dlp tải về.

Đây là adapter luôn sống. Ba crawler trending ở phase 6 có gãy thì
đường này vẫn chạy, nên pipeline không bao giờ tắc hoàn toàn.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from reup.adapters.source import Candidate, FetchResult


class ManualSource:
    name = "manual"

    def list_trending(self, region: str, limit: int) -> list[Candidate]:
        raise NotImplementedError(
            "nguồn manual không quét trending — dán link bằng `reup add <url>`"
        )

    def fetch(self, url: str, dest: Path) -> FetchResult:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        info_path = dest.parent / "source.info.json"

        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--write-info-json",
            "--merge-output-format", "mp4",
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
            "-o", str(dest),
            url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"yt-dlp thoát với mã {proc.returncode} khi tải {url}\n"
                f"stderr:\n{proc.stderr}"
            )

        produced = dest.with_suffix(".info.json")
        if produced.exists() and produced != info_path:
            produced.replace(info_path)
        if not info_path.exists():
            info_path.write_text("{}", encoding="utf-8")

        raw = json.loads(info_path.read_text(encoding="utf-8"))
        return FetchResult(
            video_path=dest,
            info_path=info_path,
            duration_ms=round(float(raw.get("duration", 0)) * 1000),
            width=int(raw.get("width", 0)),
            height=int(raw.get("height", 0)),
        )
