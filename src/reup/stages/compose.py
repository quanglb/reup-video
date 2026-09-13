"""Stage 12 — dựng video cuối bằng một lượt ffmpeg duy nhất.

Thứ tự filter theo spec §7.3 và là bắt buộc:

    che vùng sub gốc (blur + phủ trắng, theo từng câu) → transform → dán sub Việt → encode

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
from reup.textcolor import BLACK, from_hex
from reup.media.ffmpeg import has_filter, probe, run_ffmpeg
from reup.models import Transcript
from reup.subtitle import (
    SIDE_MARGIN_RATIO,
    Overlay,
    find_font,
    place_in_box,
    render_fitted,
    render_line,
    vertical_position,
)

# VideoToolbox kém hiệu quả hơn libx264 ở cùng bitrate (spec R6), nên phải cấp
# thêm chỗ so với nguồn. 1.6x là mức bù đủ mà không phình file.
HEADROOM = 1.6
# Che chữ gốc kiểu kính mờ: blur nhiều lượt rồi phủ một lớp trắng rất mỏng (10%).
# Blur mới là thứ xoá nét chữ; lớp trắng chỉ ám nhẹ cho vùng che trông như
# tấm kính mờ thay vì một mảng nhoè bẩn, và không lấn át hình phía sau.
BLUR_RADIUS = 20
BLUR_PASSES = 3
COVER_COLOR = "white"
COVER_OPACITY = 0.1
# Đệm quanh hộp OCR: hộp của Vision ôm sát nét chữ, không đệm là lộ viền chữ.
COVER_PAD_RATIO = 0.3
COVER_PAD_MIN = 10
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

    Nguồn được `split` MỘT lần thành khung nền + mỗi vùng một nhánh
    crop -> boxblur -> phủ trắng, rồi các nhánh overlay lần lượt lên nền.
    Đừng split nối tiếp từng vùng: mỗi tầng split lồng nhau làm hàng đợi khung
    trong filtergraph phình theo cấp số nhân — 16 vùng mất vài giây, 31 vùng
    thì ffmpeg treo ở frame=0.
    Vùng có `start_ms`/`end_ms` chỉ được che trong khoảng đó: mỗi câu gốc một
    khung riêng, đổi theo câu. Toạ độ tính trên khung GỐC nên khâu này phải
    chạy trước mọi phép biến hình.
    """
    if not regions:
        return "", "[0:v]"

    branches = "".join(f"[reg{i}]" for i in range(len(regions)))
    parts = [f"[0:v]split={len(regions) + 1}[base]{branches}"]
    current = "[base]"
    for i, r in enumerate(regions):
        geom = f"{r['w']}:{r['h']}:{r['x']}:{r['y']}"
        radius = blur_radius_for(r["w"], r["h"])
        parts.append(
            f"[reg{i}]crop={geom},boxblur={radius}:{BLUR_PASSES},"
            f"drawbox=x=0:y=0:w=iw:h=ih:color={COVER_COLOR}@{COVER_OPACITY}:t=fill[bl{i}]"
        )
        enable = ""
        if "start_ms" in r and "end_ms" in r:
            enable = (
                f":enable='between(t,{r['start_ms'] / 1000:.3f},{r['end_ms'] / 1000:.3f})'"
            )
        nxt = f"[cl{i}]"
        parts.append(f"{current}[bl{i}]overlay={r['x']}:{r['y']}{enable}{nxt}")
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


def _static_regions(job: Job) -> list[dict]:
    """Vùng cố định từ subrect.json (hợp mọi hộp chữ của cả video)."""
    if not job.subrect_json.exists():
        return []
    raw = json.loads(job.subrect_json.read_text(encoding="utf-8"))
    return [r for r in raw.get("regions", []) if r["kind"] in ("subtitle", "watermark")]


def load_cover_windows(job: Job) -> list[dict]:
    """Mỗi câu gốc một khung che: đúng hộp chữ của câu, đúng lúc câu hiện.

    Đọc từ ocr.json (bản mới có `box`). OCR lấy mẫu 2 khung/giây nên mốc thời
    gian lệch tối đa nửa bước — nới mỗi đầu nửa bước để không lộ chữ lúc chuyển
    câu. Job cũ chưa có `box` thì trả rỗng, compose lùi về vùng cố định.
    """
    if not job.ocr_json.exists():
        return []
    raw = json.loads(job.ocr_json.read_text(encoding="utf-8"))
    lines = [line for line in raw.get("lines", []) if line.get("box")]
    if not lines:
        return []
    vw, vh = raw.get("video_w"), raw.get("video_h")
    if not (vw and vh):
        info = probe(job.source_video)
        vw, vh = info.width, info.height
    half = int(raw.get("step_ms") or 500) // 2

    windows = []
    for line in lines:
        b = line["box"]
        pad_y = max(COVER_PAD_MIN, round(b["h"] * COVER_PAD_RATIO))
        pad_x = max(COVER_PAD_MIN, round(b["h"] * COVER_PAD_RATIO * 1.5))
        x, y = max(0, b["x"] - pad_x), max(0, b["y"] - pad_y)
        windows.append({
            "x": x,
            "y": y,
            "w": min(vw - x, b["w"] + pad_x * 2),
            "h": min(vh - y, b["h"] + pad_y * 2),
            "kind": "subtitle",
            "start_ms": max(0, line["start_ms"] - half),
            "end_ms": line["end_ms"] + half,
            "line_h": b.get("line_h") or 0,
            "color": b.get("color") or "",
            "outline": b.get("outline") or "",
        })
    return windows


def load_blur_regions(job: Job) -> list[dict]:
    """Vùng cần che. Có khung theo từng câu thì dùng nó cho phụ đề, watermark
    vẫn che cố định. Chưa chạy subdetect/ocr thì không che gì."""
    static = _static_regions(job)
    windows = load_cover_windows(job)
    if not windows:
        return static
    return windows + [r for r in static if r["kind"] == "watermark"]


def subtitle_cover(job: Job) -> dict | None:
    """Vùng phụ đề gốc cố định rộng nhất — chỗ đặt sub Việt khi không có khung theo câu."""
    subs = [r for r in _static_regions(job) if r["kind"] == "subtitle"]
    return max(subs, key=lambda r: r["w"] * r["h"]) if subs else None


def window_for(seg_start: int, seg_end: int, windows: list[dict], reach_ms: int = 1500):
    """Khung che trùng thời gian với câu Việt nhiều nhất; không trùng thì khung gần nhất."""
    best, best_overlap = None, 0
    for w in windows:
        overlap = min(seg_end, w["end_ms"]) - max(seg_start, w["start_ms"])
        if overlap > best_overlap:
            best, best_overlap = w, overlap
    if best is not None:
        return best
    near = [
        (min(abs(w["start_ms"] - seg_end), abs(seg_start - w["end_ms"])), w)
        for w in windows
    ]
    near = [(d, w) for d, w in near if d <= reach_ms]
    return min(near, key=lambda t: t[0])[1] if near else None


def _max_size(box: dict, cfg: Config) -> int:
    """Cỡ chữ trần cho một khung: bằng chiều cao MỘT dòng chữ gốc.

    Vision đo hộp ôm nét chữ, cao cỡ 0.9 lần cỡ font — nên cỡ font Việt tương
    đương là line_h / 0.9. Khung cố định (không có line_h) thì lấy 1.5 lần cỡ
    trong config cho khỏi to quá khổ.
    """
    line_h = box.get("line_h")
    if line_h:
        return max(cfg.subtitle.size // 2, round(line_h / 0.9))
    return round(cfg.subtitle.size * 1.5)


def build_overlays(job: Job, cfg: Config) -> list[Overlay]:
    """Vẽ mỗi câu tiếng Việt ra một PNG trong suốt, kèm mốc thời gian của nó.

    Có khung chữ gốc thì chữ Việt được co giãn cho vừa khít khung đó và đặt vào
    giữa khung. Không có thì lùi về cỡ chữ và vị trí trong config.
    """
    if not job.translation_json.exists():
        return []
    info = probe(job.source_video)
    windows = load_cover_windows(job)
    cover = subtitle_cover(job)
    font = find_font(cfg.subtitle.font)
    outline_ratio = cfg.subtitle.outline / max(1, cfg.subtitle.size)
    out_dir = job.root / "subs"
    out_dir.mkdir(parents=True, exist_ok=True)

    overlays = []
    for seg in Transcript.load(job.translation_json).segments:
        if not (seg.text or "").strip():
            continue
        png = out_dir / f"sub_{seg.id:04d}.png"
        box = window_for(seg.start_ms, seg.end_ms, windows) if windows else cover
        if box:
            # Không rộng quá khung hình trừ lề, kể cả khi khung chữ gốc sát mép.
            fit_w = min(box["w"], int(info.width * (1 - SIDE_MARGIN_RATIO * 2)))
            w, h, _ = render_fitted(
                seg.text, png, font, fit_w, box["h"], outline_ratio,
                max_size=_max_size(box, cfg),
                fill=from_hex(box["color"]) if box.get("color") else None,
                stroke=from_hex(box["outline"], BLACK) if box.get("outline") else None,
            )
            x, y = place_in_box(w, h, box, info.width, info.height)
        else:
            _, h = render_line(
                seg.text, png, info.width, font, cfg.subtitle.size, cfg.subtitle.outline
            )
            x, y = 0, vertical_position(info.height, h, cfg.subtitle.position, None)
        overlays.append(
            Overlay(path=png, start_ms=seg.start_ms, end_ms=seg.end_ms, x=x, y=y)
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
