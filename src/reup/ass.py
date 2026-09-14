"""Sinh phụ đề ASS cho libass. Hàm thuần, không đụng ffmpeg.

Dùng ASS chứ không SRT vì cần kiểm soát font, viền và vị trí (spec §7.2): viền
dày sẽ ăn nốt phần rìa blur còn thò ra quanh vùng phụ đề gốc.

ASS đo thời gian theo phần trăm giây (h:mm:ss.cc), nên mốc mili giây phải làm
tròn xuống centisecond.
"""
from __future__ import annotations

from reup.models import Transcript

# Khung dựng ASS: toạ độ trong file tính theo khung này, libass tự co giãn.
PLAY_RES_X = 1080
PLAY_RES_Y = 1920

ALIGNMENT = {"bottom": 2, "middle": 5, "top": 8}
# Lề dưới: đủ để chữ không dính thanh tương tác của TikTok/Shorts.
MARGIN_V = 220


def format_time(ms: int) -> str:
    """h:mm:ss.cc — định dạng thời gian của ASS."""
    if ms < 0:
        ms = 0
    cs = ms // 10
    s, cs = divmod(cs, 100)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape(text: str) -> str:
    """Xuống dòng thành \\N; các ký tự điều khiển của ASS phải vô hiệu hoá."""
    out = (text or "").replace("\\", "\\\\")
    out = out.replace("{", "\\{").replace("}", "\\}")
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    return out.replace("\n", "\\N")


def build_ass(
    transcript: Transcript,
    font: str,
    size: int,
    outline: int,
    position: str = "bottom",
    play_res_x: int = PLAY_RES_X,
    play_res_y: int = PLAY_RES_Y,
) -> str:
    align = ALIGNMENT.get(position, ALIGNMENT["bottom"])
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        # Chữ trắng, viền đen dày, bóng nhẹ — đọc được trên mọi nền.
        f"Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},2,{align},60,60,{MARGIN_V},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = []
    for seg in transcript.segments:
        if not (seg.text or "").strip():
            continue
        lines.append(
            f"Dialogue: 0,{format_time(seg.start_ms)},{format_time(seg.end_ms)},"
            f"Default,,0,0,0,,{escape(seg.text)}"
        )
    return header + "\n".join(lines) + ("\n" if lines else "")
