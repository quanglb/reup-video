"""Stage 12 — dựng video cuối bằng một lượt ffmpeg duy nhất.

Thứ tự filter theo spec §7.3 và là bắt buộc:
    blur vùng sub gốc → transform → dán sub Việt → encode
Phase 1 chưa có blur và chưa có sub, nên chỉ còn transform.
Phase 3 chèn hai khâu kia vào đúng chỗ đã chừa sẵn dưới đây.
"""
from __future__ import annotations

from reup.config import Config, TransformConfig, parse_bitrate
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.ffmpeg import probe, run_ffmpeg

# VideoToolbox kém hiệu quả hơn libx264 ở cùng bitrate (spec R6), nên phải cấp
# thêm chỗ so với nguồn. 1.6x là mức bù đủ mà không phình file.
HEADROOM = 1.6
# Sàn cho khung 1080x1920: dưới mức này thì cảnh động bắt đầu vỡ khối.
FLOOR_BPS = 2_500_000


def pick_bitrate(source_bps: int, ceiling_bps: int) -> int:
    """Chọn bitrate encode: bù headroom trên nguồn, nhưng không vượt trần config.

    Dùng thẳng trần cho mọi clip làm file ra phình vô ích — một Short 1.6 Mbps
    encode ở 8M cho ra file nặng gấp năm lần mà không thêm chi tiết nào.
    """
    if ceiling_bps <= 0:
        raise ValueError(f"ceiling_bps phải dương, nhận {ceiling_bps}")
    if source_bps <= 0:
        # ffprobe không báo được bitrate nguồn: lấy trần cho an toàn.
        return ceiling_bps
    return min(ceiling_bps, max(FLOOR_BPS, round(source_bps * HEADROOM)))


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


def build_filter_complex(cfg: Config, has_bgm: bool) -> str:
    """Input 0 = video nguồn, 1 = dub.wav, 2 = bgm.wav (chỉ khi has_bgm)."""
    video = f"[0:v]{build_transform(cfg.transform)}[v]"
    # Phase 3: chèn crop+boxblur+overlay TRƯỚC build_transform,
    #          và ass=sub.ass SAU nó — nếu không chữ Việt sẽ bị hflip lật ngược.

    if cfg.audio.mode == "drop_original" or not has_bgm:
        audio = "[1:a]aresample=48000[a]"
    else:
        # Trộn nhạc nền ĐÃ TÁCH, không phải audio gốc: audio gốc còn nguyên
        # giọng người nói, nghe chồng lên giọng lồng tiếng.
        audio = (
            f"[2:a]volume={cfg.audio.bgm_gain},aresample=48000[bg];"
            "[bg][1:a]amix=inputs=2:duration=first:normalize=0[a]"
        )
    return f"{video};{audio}"


def run(job: Job, cfg: Config) -> None:
    job.final_mp4.parent.mkdir(parents=True, exist_ok=True)
    bitrate = pick_bitrate(
        probe(job.source_video).video_bps, parse_bitrate(cfg.profile.video_bitrate)
    )
    has_bgm = job.bgm.exists() and cfg.audio.mode != "drop_original"
    inputs = ["-i", str(job.source_video), "-i", str(job.dub_wav)]
    if has_bgm:
        inputs += ["-i", str(job.bgm)]

    run_ffmpeg([
        *inputs,
        "-filter_complex", build_filter_complex(cfg, has_bgm),
        "-map", "[v]", "-map", "[a]",
        "-c:v", cfg.profile.encoder, "-b:v", str(bitrate),
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(job.final_mp4),
    ])


SPEC = StageSpec(name="compose", produces=("render/final.mp4",), run=run)
