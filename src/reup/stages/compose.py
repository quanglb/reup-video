"""Stage 12 — dựng video cuối bằng một lượt ffmpeg duy nhất.

Thứ tự filter theo spec §7.3 và là bắt buộc:

    blur vùng sub gốc → transform → dán sub Việt → encode

`hflip` sau khi dán phụ đề sẽ lật ngược chữ tiếng Việt. Sub gốc bị lật theo
transform thì không sao vì nó đã bị blur mất rồi.

Phụ đề dán bằng **overlay ảnh PNG** chứ không phải filter `ass`: ffmpeg của
Homebrew build không có libass (xem `reup/subtitle.py`). Máy nào có libass thì
`build_filter_complex` tự chuyển sang đường `ass` cho chữ đẹp hơn.
"""
from __future__ import annotations

import json

from reup.config import Config, TransformConfig, parse_bitrate
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.ffmpeg import has_filter, probe, run_ffmpeg
from reup.models import Transcript
from reup.subtitle import Overlay, find_font, render_line, vertical_position

# VideoToolbox kém hiệu quả hơn libx264 ở cùng bitrate (spec R6), nên phải cấp
# thêm chỗ so với nguồn. 1.6x là mức bù đủ mà không phình file.
HEADROOM = 1.6
# Blur đủ mạnh để chữ gốc không đọc được, kèm giảm sáng cho phần rìa đỡ chói.
# Không cần đẹp: phụ đề Việt sẽ đè gần kín vùng này (spec §7.2).
BLUR_RADIUS = 20
BLUR_DARKEN = -0.25
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


def blur_radius_for(w: int, h: int, wanted: int = BLUR_RADIUS) -> int:
    """Bán kính blur lớn nhất dùng được cho một vùng cỡ w x h.

    yuv420p chia đôi mặt phẳng chroma, và boxblur đòi bán kính nhỏ hơn nửa
    cạnh ngắn của mặt phẳng — tức nhỏ hơn 1/4 cạnh ngắn của vùng. Vùng phụ đề
    thường dẹt (cao ~80px) nên hằng số 20 vượt giới hạn ngay.
    """
    limit = min(w, h) // 4
    return max(1, min(wanted, limit - 1))


def build_blur_chain(regions: list[dict]) -> tuple[str, str]:
    """Chuỗi filter che các vùng chữ gốc. Trả (chuỗi, nhãn đầu ra).

    Mỗi vùng một cặp crop -> boxblur -> overlay, nối tiếp nhau. Toạ độ tính
    trên khung GỐC nên khâu này phải chạy trước mọi phép biến hình.
    """
    if not regions:
        return "", "[0:v]"

    parts = []
    current = "[0:v]"
    for i, r in enumerate(regions):
        geom = f"{r['w']}:{r['h']}:{r['x']}:{r['y']}"
        radius = blur_radius_for(r["w"], r["h"])
        parts.append(f"{current}split=2[base{i}][reg{i}]")
        parts.append(
            f"[reg{i}]crop={geom},boxblur={radius}:2,"
            f"eq=brightness={BLUR_DARKEN}[bl{i}]"
        )
        nxt = f"[cl{i}]"
        parts.append(f"[base{i}][bl{i}]overlay={r['x']}:{r['y']}{nxt}")
        current = nxt
    return ";".join(parts) + ";", current


def build_subtitle_overlays(overlays: list, first_input: int, src_label: str) -> str:
    """Dán từng ảnh phụ đề vào đúng khoảng thời gian của nó.

    Mỗi câu một `overlay` kèm `enable=between(t,...)`. Ảnh chỉ được vẽ trong
    khoảng đó nên không cần ghép chúng thành video trong suốt.
    """
    if not overlays:
        return f"{src_label}null[v]"

    parts = []
    current = src_label
    for i, ov in enumerate(overlays):
        idx = first_input + i
        nxt = "[v]" if i == len(overlays) - 1 else f"[sub{i}]"
        start = ov.start_ms / 1000
        end = ov.end_ms / 1000
        parts.append(
            f"{current}[{idx}:v]overlay={ov.x}:{ov.y}:"
            f"enable='between(t,{start:.3f},{end:.3f})'{nxt}"
        )
        current = nxt
    return ";".join(parts)


def build_filter_complex(
    cfg: Config,
    has_bgm: bool,
    regions: list[dict] | None = None,
    sub_path: str | None = None,
    overlays: list | None = None,
    first_overlay_input: int = 2,
) -> str:
    """Input 0 = video nguồn, 1 = dub.wav, rồi bgm.wav và các ảnh phụ đề."""
    blur, src = build_blur_chain(regions or [])
    video = f"{blur}{src}{build_transform(cfg.transform)}"

    if sub_path:
        # Máy có libass: để libass lo, chữ đẹp hơn ảnh dựng sẵn.
        video += f"[xf];[xf]ass={_escape_filter_path(sub_path)}[v]"
    elif overlays:
        video += "[xf];" + build_subtitle_overlays(overlays, first_overlay_input, "[xf]")
    else:
        video += "[v]"

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


def _escape_filter_path(path: str) -> str:
    r"""Thoát đường dẫn cho filtergraph.

    Trong filtergraph, `:` ngăn cách tham số và `'` mở chuỗi, nên đường dẫn
    chứa chúng sẽ bẻ gãy cả chuỗi filter.
    """
    return path.replace(":", r"\:").replace("'", r"\'")


def load_blur_regions(job: Job) -> list[dict]:
    """Vùng cần che, đọc từ subrect.json. Chưa chạy subdetect thì không che gì."""
    if not job.subrect_json.exists():
        return []
    raw = json.loads(job.subrect_json.read_text(encoding="utf-8"))
    return [r for r in raw.get("regions", []) if r["kind"] in ("subtitle", "watermark")]


def subtitle_cover(job: Job) -> dict | None:
    """Vùng phụ đề gốc rộng nhất, để đặt sub Việt đè lên (spec §7.2)."""
    subs = [r for r in load_blur_regions(job) if r["kind"] == "subtitle"]
    return max(subs, key=lambda r: r["w"] * r["h"]) if subs else None


def build_overlays(job: Job, cfg: Config) -> list[Overlay]:
    """Vẽ mỗi câu tiếng Việt ra một PNG trong suốt, kèm mốc thời gian của nó."""
    if not job.translation_json.exists():
        return []
    info = probe(job.source_video)
    cover = subtitle_cover(job)
    font = find_font(cfg.subtitle.font)
    out_dir = job.root / "subs"
    out_dir.mkdir(parents=True, exist_ok=True)

    overlays = []
    for seg in Transcript.load(job.translation_json).segments:
        if not (seg.text or "").strip():
            continue
        png = out_dir / f"sub_{seg.id:04d}.png"
        _, h = render_line(
            seg.text, png, info.width, font, cfg.subtitle.size, cfg.subtitle.outline
        )
        overlays.append(
            Overlay(
                path=png,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                x=0,
                y=vertical_position(info.height, h, cfg.subtitle.position, cover),
            )
        )
    return overlays


def run(job: Job, cfg: Config) -> None:
    job.final_mp4.parent.mkdir(parents=True, exist_ok=True)
    bitrate = pick_bitrate(
        probe(job.source_video).video_bps, parse_bitrate(cfg.profile.video_bitrate)
    )
    has_bgm = job.bgm.exists() and cfg.audio.mode != "drop_original"
    inputs = ["-i", str(job.source_video), "-i", str(job.dub_wav)]
    if has_bgm:
        inputs += ["-i", str(job.bgm)]

    regions = load_blur_regions(job)
    use_ass = job.sub_ass.exists() and has_filter("ass")
    overlays = [] if use_ass else build_overlays(job, cfg)
    first_overlay_input = len(inputs) // 2
    for ov in overlays:
        inputs += ["-i", str(ov.path)]

    tmp_mp4 = job.final_mp4.with_suffix(".tmp.mp4")
    if tmp_mp4.exists():
        tmp_mp4.unlink()

    run_ffmpeg([
        *inputs,
        "-filter_complex",
        build_filter_complex(
            cfg,
            has_bgm,
            regions,
            str(job.sub_ass) if use_ass else None,
            overlays,
            first_overlay_input,
        ),
        "-map", "[v]", "-map", "[a]",
        "-c:v", cfg.profile.encoder, "-b:v", str(bitrate),
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(tmp_mp4),
    ])

    import os
    os.replace(tmp_mp4, job.final_mp4)


# Chốt B đứng sau đây: xem thành phẩm trước khi xuất (spec §8.3).
SPEC = StageSpec(
    name="compose", produces=("render/final.mp4",), run=run, gate="b"
)
