"""Rút khung hình để đưa cho OCR. Hàm thuần, không biết Job là gì."""
from __future__ import annotations

from pathlib import Path

from reup.media.ffmpeg import run_ffmpeg


def extract_frames(src: Path, out_dir: Path, fps: float = 2.0) -> list[Path]:
    """Lấy mẫu `fps` khung mỗi giây, ghi png vào `out_dir`, trả danh sách đã sắp."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f_*.png"):
        old.unlink()
    run_ffmpeg(["-i", str(src), "-vf", f"fps={fps}", str(out_dir / "f_%05d.png")])
    return sorted(out_dir.glob("f_*.png"))
