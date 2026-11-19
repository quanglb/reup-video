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
import os
from dataclasses import dataclass
from pathlib import Path

from reup import cover
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
# Che chữ gốc trên nền có hình: blur để xoá nét chữ, rồi phủ tấm nền tối gần đặc
# (xem `reup.cover`). Bản cũ phủ trắng 10% thành "kính mờ" — TikTok đọc là che
# logo và gắn nhãn không nguyên tác.
BLUR_RADIUS = 20
BLUR_PASSES = 3
COVER_COLOR = cover.PLATE_COLOR
COVER_OPACITY = cover.PLATE_OPACITY
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

    Mỗi vùng mang `fill` do `reup.cover` chọn:
    - `flat`: tô thẳng màu nền bằng `drawbox` trên luồng chính — không cần nhánh.
    - `plate` (mặc định): nhánh crop -> boxblur -> tấm nền tối gần đặc, rồi
      overlay về. KHÔNG còn kính mờ trắng 10%: ô mờ trong suốt là đúng thứ TikTok
      gọi là "hình mờ / logo bị che".

    Nguồn được `split` MỘT lần thành khung nền + mỗi vùng plate một nhánh.
    Đừng split nối tiếp từng vùng: mỗi tầng split lồng nhau làm hàng đợi khung
    trong filtergraph phình theo cấp số nhân — 16 vùng mất vài giây, 31 vùng
    thì ffmpeg treo ở frame=0.
    Vùng có `start_ms`/`end_ms` chỉ được che trong khoảng đó: mỗi câu gốc một
    khung riêng, đổi theo câu. Toạ độ tính trên khung GỐC nên khâu này phải
    chạy trước mọi phép biến hình.
    """
    if not regions:
        return "", "[0:v]"

    def enable_for(r: dict) -> str:
        if "start_ms" in r and "end_ms" in r:
            return (
                f":enable='between(t,{r['start_ms'] / 1000:.3f},{r['end_ms'] / 1000:.3f})'"
            )
        return ""

    plates = [r for r in regions if (r.get("fill") or {}).get("mode") != cover.FLAT]
    flats = [r for r in regions if (r.get("fill") or {}).get("mode") == cover.FLAT]

    parts: list[str] = []
    current = "[0:v]"
    # Tô nền phẳng TRƯỚC, dải plate SAU: hai câu chồng thời gian mà một câu tô
    # đen một câu plate thì dải plate phải nằm trên cùng, không thì lộ hai tông.
    if flats:
        boxes = ",".join(
            f"drawbox=x={r['x']}:y={r['y']}:w={r['w']}:h={r['h']}:"
            f"color={r['fill']['color']}@1:t=fill{enable_for(r)}"
            for r in flats
        )
        parts.append(f"{current}{boxes}[flat]")
        current = "[flat]"
    if plates:
        branches = "".join(f"[reg{i}]" for i in range(len(plates)))
        parts.append(f"{current}split={len(plates) + 1}[base]{branches}")
        current = "[base]"
    for i, r in enumerate(plates):
        geom = f"{r['w']}:{r['h']}:{r['x']}:{r['y']}"
        radius = blur_radius_for(r["w"], r["h"])
        parts.append(
            f"[reg{i}]crop={geom},boxblur={radius}:{BLUR_PASSES},"
            f"drawbox=x=0:y=0:w=iw:h=ih:color={COVER_COLOR}@{COVER_OPACITY}:t=fill[bl{i}]"
        )
        nxt = f"[cl{i}]"
        parts.append(f"{current}[bl{i}]overlay={r['x']}:{r['y']}{enable_for(r)}{nxt}")
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


def annotate_fills(job: Job, regions: list[dict], video_w: int = 0) -> list[dict]:
    """Gắn cách che (`flat` tô màu nền / `plate` tấm nền tối) cho từng vùng.

    Vùng phụ đề phải dùng plate thì nới thành một dải kéo hết bề ngang khung:
    một băng phụ đề tối trông như thiết kế có chủ ý, còn miếng vá đúng cỡ chữ
    gốc (rộng hơn chữ Việt) thì lòi hai mẩu tối hai bên chữ.
    """
    frames = sorted((job.root / "frames").glob("f_*.png"))
    fills = [cover.classify_region(frames, r, others=regions) for r in regions]
    subs = [i for i, r in enumerate(regions) if r.get("kind") == "subtitle"]
    plated = sum(1 for i in subs if fills[i]["mode"] == cover.PLATE)
    # Nhiều câu phải dùng dải thì dùng dải cho mọi câu: phụ đề cả video cùng một
    # kiểu, không lúc có băng lúc không. Trên nền đen dải tối gần như vô hình.
    band_all = bool(subs) and plated >= len(subs) * BAND_ALL_RATIO
    out = []
    for i, r in enumerate(regions):
        fill = fills[i]
        if band_all and i in subs:
            fill = {"mode": cover.PLATE}
        r = {**r, "fill": fill}
        if fill["mode"] == cover.PLATE and r.get("kind") == "subtitle" and video_w:
            r["x"], r["w"] = 0, video_w
        out.append(r)
    if band_all and video_w:
        sub_items = [
            r for r in out
            if r.get("kind") == "subtitle" and (r.get("fill") or {}).get("mode") == cover.PLATE
        ]
        if len(sub_items) > 1:
            y0 = min(r["y"] for r in sub_items)
            y1 = max(r["y"] + r["h"] for r in sub_items)
            single_band = {
                "x": 0,
                "y": y0,
                "w": video_w,
                "h": y1 - y0,
                "kind": "subtitle",
                "fill": {"mode": cover.PLATE},
            }
            out = [r for r in out if r.get("kind") != "subtitle"] + [single_band]
    return out



# Bề ngang tối thiểu dành cho một câu phụ đề Việt, so với bề ngang khung.
SUB_WIDTH_RATIO = 0.8
# Từ ngần này phần câu phải dùng dải plate trở lên thì cả video dùng dải.
BAND_ALL_RATIO = 0.3


# Chữ cố định dịch ra được nới ngang tới ngần này bề ngang khung khi nền phẳng:
# tiếng Việt dài gấp 2-3 lần chữ Hán, nhét vừa hộp cũ thì chữ bé tí.
HEADER_WIDTH_RATIO = 0.88


def _same_box(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("x", "y", "w", "h"))


def header_overlays(job: Job, cfg: Config, regions: list[dict], duration_ms: int,
                    video_w: int, video_h: int) -> list[Overlay]:
    """Bản dịch chữ cố định (tiêu đề cháy sẵn của video gốc), đặt lại đúng chỗ.

    Nền phẳng: vùng đã được tô màu nền, chữ Việt nới ngang cho dễ đọc. Nền có
    hình: chữ nằm gọn trong tấm nền tối. Vùng nền có hình mà không có bản dịch
    thì in tiêu đề video — không bao giờ để một tấm nền trống suốt video.
    """
    if not job.meta_json.exists():
        return []
    meta = json.loads(job.meta_json.read_text(encoding="utf-8"))
    headers = meta.get("headers") or []
    if not headers:
        return []
    part = job.settings.get("part")
    if part:
        part_tag = f" # Phần {part}"
        valid = [h for h in headers if (h.get("vi") or "").strip()]
        if valid and not any(f"Phần {part}" in h["vi"] for h in valid):
            best = max(valid, key=lambda h: len(h["vi"]))
            best["vi"] = f"{best['vi']}{part_tag}"
    from reup.textcolor import colors_from_frame

    frames = sorted((job.root / "frames").glob("f_*.png"))
    mid = frames[len(frames) // 2] if frames else None
    font = find_font(cfg.subtitle.font)
    out_dir = job.root / "subs"
    out_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for i, h in enumerate(headers):
        region = next((r for r in regions if _same_box(r, h)), None)
        mode = ((region or {}).get("fill") or {}).get("mode", cover.PLATE)
        text = (h.get("vi") or "").strip()
        if not text and mode == cover.PLATE:
            text = (meta.get("title") or "")[:42].strip()
        if not text:
            continue
        box = {k: h[k] for k in ("x", "y", "w", "h")}
        colors = colors_from_frame(mid, box) if mid else {}
        line_h = max(12, box["h"] - 16)  # subdetect đệm 8px mỗi phía
        if mode == cover.FLAT:
            want = max(box["w"], int(video_w * HEADER_WIDTH_RATIO))
            cx = box["x"] + box["w"] / 2
            x = int(max(0, min(video_w - want, cx - want / 2)))
            box = {**box, "x": x, "w": min(want, video_w)}
        png = out_dir / f"header_{i:02d}.png"
        w, hh, _ = render_fitted(
            text, png, font, box["w"], box["h"],
            cfg.subtitle.outline / max(1, cfg.subtitle.size),
            max_size=round(line_h * 0.85),
            fill=from_hex(colors["color"]) if colors.get("color") else None,
            stroke=from_hex(colors["outline"], BLACK) if colors.get("outline") else None,
        )
        x, y = place_in_box(w, hh, box, video_w, video_h)
        items.append({"path": png, "w": w, "h": hh, "x": x, "y": y, "box": box})

    # Chống đè chữ: nếu các dòng tiêu đề bị va chạm hoặc đè lên nhau, căn chỉnh lại thành khối xếp tầng
    if len(items) > 1:
        items.sort(key=lambda it: it["y"])
        has_overlap = any(
            items[j]["y"] + items[j]["h"] + 4 > items[j + 1]["y"]
            for j in range(len(items) - 1)
        )
        if has_overlap:
            gap = 6
            orig_y0 = min(it["box"]["y"] for it in items)
            orig_y1 = max(it["box"]["y"] + it["box"]["h"] for it in items)
            orig_cy = (orig_y0 + orig_y1) / 2
            total_h = sum(it["h"] for it in items) + (len(items) - 1) * gap
            start_y = round(orig_cy - total_h / 2)
            start_y = max(0, min(video_h - total_h, start_y))

            curr_y = start_y
            for it in items:
                it["y"] = curr_y
                curr_y += it["h"] + gap

    return [
        Overlay(path=it["path"], start_ms=0, end_ms=duration_ms, x=it["x"], y=it["y"])
        for it in items
    ]


def label_regions(job: Job) -> list[dict]:
    """Vùng che cho nhãn chữ Hán trong cảnh (meta.json đã dịch), đúng lúc nó hiện."""
    if not job.meta_json.exists():
        return []
    meta = json.loads(job.meta_json.read_text(encoding="utf-8"))
    return [
        {k: lb[k] for k in ("x", "y", "w", "h", "start_ms", "end_ms")} | {"kind": "label"}
        for lb in meta.get("labels") or []
    ]


# Nhãn dịch ra được nới ngang tới ngần này bề ngang khung khi nền phẳng.
LABEL_WIDTH_RATIO = 0.5
# Cột của nhãn dọc: đủ cho một từ tiếng Việt mỗi dòng.
VERTICAL_LABEL_RATIO = 0.12


def label_overlays(job: Job, cfg: Config, regions: list[dict],
                   video_w: int, video_h: int) -> list[Overlay]:
    """Bản dịch của nhãn trong cảnh, cùng chỗ, cùng màu, cùng khoảng thời gian."""
    if not job.meta_json.exists():
        return []
    labels = json.loads(job.meta_json.read_text(encoding="utf-8")).get("labels") or []
    if not labels:
        return []
    from reup.textcolor import colors_from_frame

    frames = sorted((job.root / "frames").glob("f_*.png"))
    font = find_font(cfg.subtitle.font)
    out_dir = job.root / "subs"
    out_dir.mkdir(parents=True, exist_ok=True)
    overlays = []
    for i, lb in enumerate(labels):
        text = (lb.get("vi") or "").strip()
        if not text:
            continue
        region = next((r for r in regions if r.get("kind") == "label" and _same_box(r, lb)
                       and r.get("start_ms") == lb["start_ms"]), None)
        mode = ((region or {}).get("fill") or {}).get("mode", cover.PLATE)
        box = {k: lb[k] for k in ("x", "y", "w", "h")}
        mid = frames[min(len(frames) - 1, (lb["start_ms"] + lb["end_ms"]) // 1000)] if frames else None
        colors = colors_from_frame(mid, box) if mid else {}
        # Nhãn dọc: giữ dạng cột, chỉ nới vừa cho mỗi dòng một từ — nới ngang hẳn
        # thì chữ Việt đè lên vật mà nhãn đang chỉ (lá bài ngay cạnh).
        if lb.get("vertical"):
            want = max(box["w"], int(video_w * VERTICAL_LABEL_RATIO))
            cx = box["x"] + box["w"] / 2
            box = {**box, "x": int(max(0, min(video_w - want, cx - want / 2))), "w": min(want, video_w)}
        elif mode == cover.FLAT:
            want = max(box["w"], int(video_w * LABEL_WIDTH_RATIO))
            cx = box["x"] + box["w"] / 2
            box = {**box, "x": int(max(0, min(video_w - want, cx - want / 2))), "w": min(want, video_w)}
        png = out_dir / f"label_{i:03d}.png"
        w, hh, _ = render_fitted(
            text, png, font, box["w"], box["h"],
            cfg.subtitle.outline / max(1, cfg.subtitle.size),
            max_size=round((lb.get("line_h") or box["h"]) / 0.9),
            fill=from_hex(colors["color"]) if colors.get("color") else None,
            stroke=from_hex(colors["outline"], BLACK) if colors.get("outline") else None,
        )
        x, y = place_in_box(w, hh, box, video_w, video_h)
        overlays.append(Overlay(path=png, start_ms=lb["start_ms"], end_ms=lb["end_ms"], x=x, y=y))
    return overlays


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


@dataclass(frozen=True)
class _RenderJob:
    """Toàn bộ dữ liệu cần để vẽ một PNG phụ đề — chỉ kiểu dữ liệu thuần (str,
    int, float, Path, dict), pickle được để gửi sang tiến trình worker.

    Không truyền `Config` hay object font đã load: `Config` thì pickle được
    (mọi dataclass đều bất biến, chỉ chứa kiểu thuần) nhưng vẫn cồng kềnh hơn
    cần thiết; còn `find_font` chỉ trả về đường dẫn `Path` (không phải
    `ImageFont` đã load — bản thân `render_fitted`/`render_line` mới là nơi
    gọi `ImageFont.truetype`), nên mỗi worker tự load font từ đường dẫn này,
    y hệt tiến trình cha trước đây.
    """

    png: Path
    text: str
    start_ms: int
    end_ms: int
    box: dict | None
    font: Path
    outline_ratio: float
    max_size: int
    video_w: int
    video_h: int
    fallback_size: int
    fallback_outline: int
    fallback_position: str


def _render_one(rj: _RenderJob) -> Overlay:
    """Vẽ một PNG phụ đề. Hàm mức module (không phải closure) để
    `ProcessPoolExecutor` pickle được khi gửi sang worker."""
    if rj.box:
        # Chữ Việt dài gấp 2-3 lần chữ Hán: ép vừa bề ngang chữ gốc thì chữ bé và
        # xuống dòng. Vùng gốc đã được che (dải phụ đề kéo hết ngang, hoặc tô nền
        # phẳng) nên cho chữ rộng tới SUB_WIDTH_RATIO khung; cỡ chữ vẫn bị trần
        # theo chiều cao dòng gốc. Không rộng quá khung hình trừ lề.
        fit_w = min(
            max(rj.box["w"], int(rj.video_w * SUB_WIDTH_RATIO)),
            int(rj.video_w * (1 - SIDE_MARGIN_RATIO * 2)),
        )
        w, h, _ = render_fitted(
            rj.text, rj.png, rj.font, fit_w, rj.box["h"], rj.outline_ratio,
            max_size=rj.max_size,
            fill=from_hex(rj.box["color"]) if rj.box.get("color") else None,
            stroke=from_hex(rj.box["outline"], BLACK) if rj.box.get("outline") else None,
        )
        x, y = place_in_box(w, h, rj.box, rj.video_w, rj.video_h)
    else:
        _, h = render_line(
            rj.text, rj.png, rj.video_w, rj.font, rj.fallback_size, rj.fallback_outline
        )
        x, y = 0, vertical_position(rj.video_h, h, rj.fallback_position, None)
    return Overlay(path=rj.png, start_ms=rj.start_ms, end_ms=rj.end_ms, x=x, y=y)


def _render_workers(n_jobs: int) -> int:
    """Số worker cho pool vẽ PNG: bị chặn bởi số lõi CPU vì đây là việc thuần
    CPU-bound (Pillow), không phải bởi `profile.concurrency` — cấu hình đó
    đếm số JOB chạy song song (một trục khác hẳn), dùng nó ở đây dễ vượt quá
    số lõi máy khi nhiều job cùng compose một lúc."""
    cores = os.cpu_count() or 1
    return max(1, min(cores, n_jobs))


def build_overlays(job: Job, cfg: Config) -> list[Overlay]:
    """Vẽ mỗi câu tiếng Việt ra một PNG trong suốt, kèm mốc thời gian của nó.

    Có khung chữ gốc thì chữ Việt được co giãn cho vừa khít khung đó và đặt vào
    giữa khung. Không có thì lùi về cỡ chữ và vị trí trong config.

    Từng câu độc lập hoàn toàn với nhau nên việc vẽ (CPU-bound, Pillow) được
    dàn qua `ProcessPoolExecutor` — khác `tts.synthesize_all` dùng
    `ThreadPoolExecutor` vì việc đó chờ mạng (I/O-bound), việc này thì không:
    thread ở đây sẽ giẫm chân nhau qua GIL. Kết quả vẫn trả về đúng thứ tự câu
    gốc (map theo index, không theo thứ tự hoàn thành) vì `run()` dùng thứ tự
    này để khớp `-i` với `overlay=...:enable=between(...)` trong filtergraph.
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

    render_jobs: list[_RenderJob] = []
    for seg in Transcript.load(job.translation_json).segments:
        if not (seg.text or "").strip():
            continue
        png = out_dir / f"sub_{seg.id:04d}.png"
        box = window_for(seg.start_ms, seg.end_ms, windows) if windows else cover
        render_jobs.append(
            _RenderJob(
                png=png,
                text=seg.text,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                box=box,
                font=font,
                outline_ratio=outline_ratio,
                max_size=_max_size(box, cfg) if box else 0,
                video_w=info.width,
                video_h=info.height,
                fallback_size=cfg.subtitle.size,
                fallback_outline=cfg.subtitle.outline,
                fallback_position=cfg.subtitle.position,
            )
        )

    if len(render_jobs) <= 1:
        return [_render_one(rj) for rj in render_jobs]

    from concurrent.futures import ProcessPoolExecutor

    workers = _render_workers(len(render_jobs))
    if workers == 1:
        return [_render_one(rj) for rj in render_jobs]

    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_render_one, render_jobs))


def run(job: Job, cfg: Config) -> None:
    job.final_mp4.parent.mkdir(parents=True, exist_ok=True)
    source_info = probe(job.source_video)
    bitrate = pick_bitrate(
        source_info.video_bps, parse_bitrate(cfg.profile.video_bitrate)
    )
    # Sàn 60s cho clip ngắn/máy chậm; clip dài thì cho gấp 4 lần độ dài nguồn.
    timeout_s = max(60.0, source_info.duration_ms / 1000 * 4)
    has_bgm = job.bgm.exists() and cfg.audio.mode != "drop_original"
    inputs = ["-i", str(job.source_video), "-i", str(job.dub_wav)]
    if has_bgm:
        inputs += ["-i", str(job.bgm)]

    regions = annotate_fills(
        job, load_blur_regions(job) + label_regions(job), source_info.width
    )
    use_ass = job.sub_ass.exists() and has_filter("ass")
    overlays = [] if use_ass else build_overlays(job, cfg)
    if not use_ass:
        overlays += header_overlays(
            job, cfg, regions, source_info.duration_ms,
            source_info.width, source_info.height,
        )
        overlays += label_overlays(job, cfg, regions, source_info.width, source_info.height)
    first_overlay_input = len(inputs) // 2
    for ov in overlays:
        # -threads 1 trước -i: mỗi PNG phụ đề là một decoder riêng, mặc định mỗi
        # decoder mở cả chục thread. Video dài có ~340 PNG → vượt giới hạn thread
        # của người dùng macOS ("pthread_create() failed", ffmpeg thoát 221).
        inputs += ["-threads", "1", "-i", str(ov.path)]

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
    ], timeout_s=timeout_s)

    import os
    os.replace(tmp_mp4, job.final_mp4)


# Chốt B đứng sau đây: xem thành phẩm trước khi xuất (spec §8.3).
SPEC = StageSpec(
    name="compose", produces=("render/final.mp4",), run=run, gate="b"
)
