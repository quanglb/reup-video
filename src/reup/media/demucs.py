"""Bọc Demucs để tách giọng người khỏi nhạc nền. Hàm thuần, không biết Job là gì.

Demucs xuất 44.1kHz stereo (tần số gốc của model), không phải 48k, nên người
gọi phải resample trước khi đưa vào timeline.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DEFAULT_MODEL = "htdemucs"
# Trên Apple Silicon, MPS nhanh hơn CPU nhiều mà gần như không sinh nhiệt —
# thiết yếu cho máy Air không quạt (spec R3).
DEFAULT_DEVICE = "mps"


class DemucsError(RuntimeError):
    pass


def _is_oom(stderr: str) -> bool:
    low = stderr.lower()
    return any(
        s in low
        for s in ("out of memory", "cannot allocate", "mps backend out of memory")
    )


def separate(
    src: Path,
    out_dir: Path,
    model: str = DEFAULT_MODEL,
    segment: int = 7,
    device: str = DEFAULT_DEVICE,
) -> tuple[Path, Path]:
    """Tách `src` thành (vocals, nhạc nền). Trả về hai đường dẫn 44.1kHz stereo.

    Hết bộ nhớ thì giảm `segment` còn một nửa rồi thử lại đúng một lần (spec §12).
    Chia nhỏ hơn thì chậm hơn nhưng vẫn xong, còn hơn để job chết.
    """
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fallback = max(2, segment // 2)
    attempts = (segment, fallback)
    for index, seg in enumerate(attempts):
        cmd = [
            sys.executable, "-m", "demucs",
            "--two-stems=vocals",
            "-n", model,
            "--segment", str(seg),
            "-d", device,
            "-o", str(out_dir),
            str(src),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            break

        last = index == len(attempts) - 1
        if _is_oom(proc.stderr):
            if last:
                raise DemucsError(
                    f"demucs hết bộ nhớ cả ở segment={segment} lẫn {fallback}. "
                    "Giảm profile.demucs_segment, hoặc đặt "
                    'audio.mode = "drop_original" để bỏ hẳn khâu này'
                )
            continue  # thử lại với lát nhỏ hơn — chậm hơn nhưng vẫn xong
        # Lỗi không phải bộ nhớ thì thử lại cũng vô ích.
        raise DemucsError(
            f"demucs thoát với mã {proc.returncode} (segment={seg})\n"
            f"stderr:\n{proc.stderr[-2000:]}"
        )

    stem = out_dir / model / src.stem
    vocals = stem / "vocals.wav"
    bgm = stem / "no_vocals.wav"
    missing = [p for p in (vocals, bgm) if not p.exists()]
    if missing:
        raise DemucsError(f"demucs chạy xong nhưng thiếu file: {missing}")
    return vocals, bgm
