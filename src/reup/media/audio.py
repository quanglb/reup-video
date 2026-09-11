"""Thao tác audio bằng ffmpeg. Hàm thuần — nhận Path vào, ghi Path ra."""
from __future__ import annotations

from pathlib import Path

from reup.media.ffmpeg import probe, run_ffmpeg


def duration_ms(path: Path) -> int:
    return probe(path).duration_ms


def extract_audio(src: Path, out: Path, sample_rate: int, channels: int) -> None:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(src), "-vn",
        "-ac", str(channels), "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", str(out),
    ])


def atempo_filter(ratio: float) -> str:
    """ffmpeg atempo chỉ nhận 0.5–2.0 nên phải ghép chuỗi khi ra ngoài khoảng đó."""
    if ratio <= 0:
        raise ValueError(f"ratio phải dương, nhận {ratio}")
    parts: list[str] = []
    remaining = ratio
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def apply_tempo(src: Path, out: Path, ratio: float) -> None:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(src), "-filter:a", atempo_filter(ratio),
        "-c:a", "pcm_s16le", str(out),
    ])


def silence(out: Path, ms: int, sample_rate: int = 48000, channels: int = 2) -> None:
    layout = "mono" if channels == 1 else "stereo"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={layout}",
        "-t", f"{ms / 1000:.3f}", "-c:a", "pcm_s16le", str(out),
    ])


def build_timeline(
    placements: list[tuple[int, Path]], total_ms: int, out: Path
) -> None:
    """Đặt mỗi wav vào mốc start_ms của nó trên nền im lặng dài total_ms."""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    if not placements:
        silence(out, total_ms)
        return

    args: list[str] = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    for _, path in placements:
        args += ["-i", str(path)]

    chains: list[str] = []
    labels: list[str] = ["[0:a]"]
    for idx, (start_ms, _) in enumerate(placements, start=1):
        label = f"[d{idx}]"
        chains.append(f"[{idx}:a]adelay={start_ms}|{start_ms},aresample=48000{label}")
        labels.append(label)

    chains.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[out]"
    )
    args += [
        "-filter_complex", ";".join(chains),
        "-map", "[out]", "-t", f"{total_ms / 1000:.3f}",
        "-c:a", "pcm_s16le", str(out),
    ]
    run_ffmpeg(args)


def to_wav(src: Path, out: Path, sample_rate: int = 48000, channels: int = 2) -> None:
    """Chuyển audio bất kỳ sang wav PCM đúng định dạng timeline dùng.

    CapCut trả mp3 24kHz mono; `build_timeline` cần wav 48kHz stereo.
    """
    extract_audio(src, out, sample_rate=sample_rate, channels=channels)
