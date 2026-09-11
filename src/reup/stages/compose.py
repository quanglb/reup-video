"""Stage 12 — dựng video cuối bằng một lượt ffmpeg duy nhất.

Thứ tự filter theo spec §7.3 và là bắt buộc:
    blur vùng sub gốc → transform → dán sub Việt → encode
Phase 1 chưa có blur và chưa có sub, nên chỉ còn transform.
Phase 3 chèn hai khâu kia vào đúng chỗ đã chừa sẵn dưới đây.
"""
from __future__ import annotations

from reup.config import Config, TransformConfig
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.ffmpeg import run_ffmpeg


def build_transform(transform: TransformConfig) -> str:
    parts: list[str] = []
    if transform.zoom != 1.0:
        z = transform.zoom
        parts.append(f"scale=iw*{z:.4f}:ih*{z:.4f}")
        parts.append("crop=iw/%.4f:ih/%.4f" % (z, z))
    if transform.hflip:
        parts.append("hflip")
    if transform.speed != 1.0:
        parts.append(f"setpts={1 / transform.speed:.6f}*PTS")
    return ",".join(parts) if parts else "null"


def build_filter_complex(cfg: Config) -> str:
    # Input 0 = video nguồn, input 1 = track lồng tiếng (dub.wav)
    video = f"[0:v]{build_transform(cfg.transform)}[v]"
    # Phase 3: chèn crop+boxblur+overlay TRƯỚC build_transform,
    #          và ass=sub.ass SAU nó — nếu không chữ Việt sẽ bị hflip lật ngược.

    if cfg.audio.mode == "drop_original":
        audio = "[1:a]aresample=48000[a]"
    else:
        audio = (
            f"[0:a]volume={cfg.audio.bgm_gain}[bg];"
            "[bg][1:a]amix=inputs=2:duration=first:normalize=0[a]"
        )
    return f"{video};{audio}"


def run(job: Job, cfg: Config) -> None:
    job.final_mp4.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(job.source_video),
        "-i", str(job.dub_wav),
        "-filter_complex", build_filter_complex(cfg),
        "-map", "[v]", "-map", "[a]",
        "-c:v", cfg.profile.encoder, "-b:v", "8M",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(job.final_mp4),
    ])


SPEC = StageSpec(name="compose", produces=("render/final.mp4",), run=run)
