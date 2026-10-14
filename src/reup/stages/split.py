"""Tách video dài thành nhiều phần ~2 phút, cắt theo mạch nội dung.

Chỉ chạy khi người dùng bấm "Tách" lúc chọn video (job.json có khoá `split`).
Job cha chỉ chạy fetch → precheck → demux → asr (bỏ Demucs: nghe tiếng gốc là
đủ để biết nội dung) rồi tới đây: AI đọc transcript, chọn chỗ cắt sao cho mỗi
phần trọn ý — "9 cách làm X" thành 3 phần, mỗi phần 3 cách. Mỗi phần thành một
job con có sẵn `source.mp4` đã cắt, nên job con bỏ qua bước tải và chạy đủ
pipeline như video thường.

Job cha dừng ở đây; người chạy (web runner, script hàng đợi) gọi
`register_children` để đưa job con vào sổ và chạy tiếp.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from reup.config import Config
from reup.core.job import Job, create_job, load_job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.media.ffmpeg import probe, run_ffmpeg

SPLIT_FILE = "split.json"
DEFAULT_MAX_S = 120
# AI được phép vượt mốc 2 phút một chút để giữ trọn ý; quá mức này là chia sai.
OVERSHOOT = 1.35
MIN_TAIL_MS = 20_000  # phần cuối ngắn hơn thế thì gộp vào phần trước

SPLIT_SCHEMA = {
    "type": "object",
    "properties": {
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_id": {"type": "integer"},
                    "end_id": {"type": "integer"},
                    "topic": {"type": "string"},
                },
                "required": ["start_id", "end_id", "topic"],
            },
        }
    },
    "required": ["parts"],
}


def requested(job: Job) -> bool:
    return bool(job.settings.get("split"))


def max_ms(job: Job) -> int:
    opts = job.settings.get("split")
    max_s = opts.get("max_s") if isinstance(opts, dict) else None
    return int(max_s or DEFAULT_MAX_S) * 1000


def plan(job: Job) -> dict | None:
    path = job.root / SPLIT_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _mmss(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


def build_prompt(segments: list[dict], total_ms: int, limit_ms: int) -> str:
    target = max(2, math.ceil(total_ms / limit_ms))
    lines = "\n".join(
        f"[{s['id']}] {_mmss(s['start_ms'])}-{_mmss(s['end_ms'])} {s['text']}"
        for s in segments
    )
    return (
        "Bạn là biên tập viên video ngắn. Video dưới đây dài "
        f"{_mmss(total_ms)}, cần tách thành nhiều video, mỗi video khoảng "
        f"{limit_ms // 1000} giây (tối đa {round(limit_ms * OVERSHOOT / 1000)} giây), "
        f"gợi ý khoảng {target} phần.\n\n"
        "Quy tắc:\n"
        "- Chia theo MẠCH NỘI DUNG, không cắt giữa một ý hay một câu.\n"
        "- Nếu video là danh sách (vd. \"9 cách làm X\", \"5 mẹo\"), chia sao cho mỗi phần "
        "chứa trọn một số mục, càng đều càng tốt (9 cách → 3 phần × 3 cách).\n"
        "- Phần mở đầu/giới thiệu đi cùng phần 1; lời kết đi cùng phần cuối.\n"
        "- Các phần nối liền nhau: start_id của phần sau = end_id phần trước + 1; "
        "phần đầu bắt đầu từ câu đầu tiên, phần cuối kết thúc ở câu cuối cùng.\n"
        "- topic: mô tả ngắn bằng tiếng Việt nội dung phần đó (vd. \"Cách 1-3: ...\").\n\n"
        "Trả về JSON: {\"parts\": [{\"start_id\": int, \"end_id\": int, \"topic\": str}]}\n\n"
        f"Transcript (mỗi dòng: [id] bắt đầu-kết thúc nội dung):\n{lines}"
    )


def _valid(parts: list[dict], segments: list[dict], limit_ms: int) -> list[tuple[int, int, str]] | None:
    """Kiểm câu trả lời của AI; sai một chỗ là bỏ cả, dùng cách chia dự phòng."""
    if not isinstance(parts, list) or len(parts) < 2:
        return None
    index = {s["id"]: i for i, s in enumerate(segments)}
    out: list[tuple[int, int, str]] = []
    expect = 0
    for p in parts:
        try:
            a, b = index[int(p["start_id"])], index[int(p["end_id"])]
        except (KeyError, TypeError, ValueError):
            return None
        if a != expect or b < a:
            return None
        span = segments[b]["end_ms"] - segments[a]["start_ms"]
        if span > limit_ms * OVERSHOOT:
            return None
        out.append((a, b, str(p.get("topic") or "").strip()))
        expect = b + 1
    if expect != len(segments):
        return None
    return out


def fallback(segments: list[dict], limit_ms: int) -> list[tuple[int, int, str]]:
    """Không có AI: cắt ở khoảng lặng dài nhất trong cửa sổ 60–100% giới hạn."""
    out: list[tuple[int, int, str]] = []
    start = 0
    n = len(segments)
    while start < n:
        base = segments[start]["start_ms"]
        if segments[-1]["end_ms"] - base <= limit_ms * 1.1:
            out.append((start, n - 1, ""))
            break
        best, best_gap = None, -1
        for i in range(start, n - 1):
            end = segments[i]["end_ms"] - base
            if end > limit_ms:
                break
            if end >= limit_ms * 0.6:
                gap = segments[i + 1]["start_ms"] - segments[i]["end_ms"]
                if gap > best_gap:
                    best, best_gap = i, gap
        if best is None:  # câu đầu đã dài quá cửa sổ: cắt ngay sau câu cuối còn vừa
            best = start
            while best + 1 < n and segments[best + 1]["end_ms"] - base <= limit_ms:
                best += 1
        out.append((start, best, ""))
        start = best + 1
    if len(out) >= 2:
        a, b, _ = out[-1]
        if segments[b]["end_ms"] - segments[a]["start_ms"] < MIN_TAIL_MS:
            out[-2] = (out[-2][0], b, "")
            out.pop()
    return out


def cut_points(
    groups: list[tuple[int, int, str]], segments: list[dict], total_ms: int
) -> list[dict]:
    """Ranh giới cắt nằm giữa khoảng lặng giữa hai câu, để không xén mất tiếng."""
    parts = []
    for k, (a, b, topic) in enumerate(groups):
        start = 0 if k == 0 else (segments[a - 1]["end_ms"] + segments[a]["start_ms"]) // 2
        parts.append({"start_ms": start, "topic": topic})
    for k, p in enumerate(parts):
        p["end_ms"] = parts[k + 1]["start_ms"] if k + 1 < len(parts) else total_ms
    return parts


def choose_parts(job: Job, cfg: Config, llm=None) -> tuple[list[dict], str]:
    """Trả (các phần, cách chia). Danh sách rỗng = không cần tách."""
    total_ms = probe(job.source_video).duration_ms
    limit = max_ms(job)
    if total_ms <= limit * 1.1:
        return [], "ngắn"
    segments = [
        s for s in json.loads(job.asr_json.read_text(encoding="utf-8")).get("segments", [])
        if str(s.get("text") or "").strip()
    ]
    if len(segments) < 2:
        n = math.ceil(total_ms / limit)
        step = total_ms // n
        return [
            {"start_ms": i * step, "end_ms": total_ms if i == n - 1 else (i + 1) * step, "topic": ""}
            for i in range(n)
        ], "đều"

    groups, how = None, "ai"
    try:
        if llm is None:
            from reup.adapters.registry import make_llm

            llm = make_llm(cfg, "translate")
        answer = llm.complete_json(build_prompt(segments, total_ms, limit), SPLIT_SCHEMA)
        groups = _valid(answer.get("parts"), segments, limit)
    except Exception:  # AI hỏng không được làm hỏng việc tách
        groups = None
    if groups is None:
        groups, how = fallback(segments, limit), "khoảng lặng"
    if len(groups) < 2:
        return [], how
    return cut_points(groups, segments, total_ms), how


def _cut(src: Path, dest: Path, start_ms: int, end_ms: int) -> None:
    tmp = dest.with_name(dest.stem + ".part.mp4")
    run_ffmpeg([
        "-ss", f"{start_ms / 1000:.3f}", "-i", str(src),
        "-t", f"{(end_ms - start_ms) / 1000:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(tmp),
    ])
    tmp.replace(dest)


def child_id(job: Job, i: int) -> str:
    return f"{job.id}-p{i}"


def run_with(job: Job, cfg: Config, llm=None) -> dict:
    parts, how = choose_parts(job, cfg, llm)
    if not parts:
        # Không cần tách: bỏ asr.json làm trên tiếng gốc (chưa qua Demucs) để
        # pipeline đầy đủ nhận diện lại trên giọng đã tách nhạc.
        job.asr_json.unlink(missing_ok=True)
        result = {"parts": [], "method": how}
        atomic_write(job.root / SPLIT_FILE, json.dumps(result, ensure_ascii=False, indent=2))
        return result

    info = {}
    if job.source_info.exists():
        info = json.loads(job.source_info.read_text(encoding="utf-8"))
    base_title = str(info.get("title") or "").strip()
    settings = job.settings
    n = len(parts)
    jobs_dir = job.root.parent
    for i, p in enumerate(parts, start=1):
        child = create_job(jobs_dir, job.source_url, job.source_lang, child_id(job, i))
        _cut(job.source_video, child.source_video, p["start_ms"], p["end_ms"])
        part_info = {
            **info,
            "title": f"{base_title} (Phần {i}/{n})" if base_title else f"Phần {i}/{n}",
            "duration": round((p["end_ms"] - p["start_ms"]) / 1000, 3),
            "part": i,
            "parts": n,
            "part_topic": p["topic"],
        }
        atomic_write(child.source_info, json.dumps(part_info, ensure_ascii=False, indent=2))
        name = str(settings.get("name") or base_title or job.id)
        child.update_settings(
            parent=job.id,
            part=i,
            parts=n,
            part_topic=p["topic"] or None,
            name=f"{name} — Phần {i}/{n}",
        )
        p["job_id"] = child.id
    result = {"parts": parts, "method": how}
    atomic_write(job.root / SPLIT_FILE, json.dumps(result, ensure_ascii=False, indent=2))
    job.update_settings(name=f"✂ Đã tách {n} phần — {settings.get('name') or base_title or job.id}")
    return result


def run(job: Job, cfg: Config) -> None:
    run_with(job, cfg)


def register_children(job: Job, store) -> list[str]:
    """Đưa job con vào sổ (pending) nếu chưa có. Trả id các job con cần chạy.

    Gọi nhiều lần vẫn an toàn: job con đã có trong sổ thì giữ nguyên trạng thái.
    """
    result = plan(job) or {}
    ids = []
    for p in result.get("parts") or []:
        cid = p.get("job_id")
        if not cid:
            continue
        try:
            child = load_job(job.root.parent, cid)
        except FileNotFoundError:
            continue
        row = store.get_job(cid)
        if row is None:
            store.upsert_job(cid, child.source_url, "pending")
            ids.append(cid)
        elif row.get("status") in ("pending", "failed"):
            ids.append(cid)
    return ids


SPEC = StageSpec(name="split", produces=(SPLIT_FILE,), run=run)
