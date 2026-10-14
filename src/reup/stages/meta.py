"""Sinh tiêu đề / mô tả / hashtag và dịch chữ cố định của video gốc.

Chạy TRƯỚC khi render (compose) vì hai thứ được in lên hình:
- chữ cố định cháy sẵn của video gốc (thường là tiêu đề chữ Hán ở nửa trên).
  Trước đây vùng này bị blur suốt video thành một ô kính mờ trống — đúng dấu
  hiệu "che logo" làm TikTok gắn nhãn không nguyên tác. Giờ nó được dịch và đặt
  lại đúng chỗ.
- tiêu đề tiếng Việt.

Một request LLM cho tất cả. Luật chữ (`reup.policy.rules`) chạy sau đó, miễn
phí; chỉ khi tiêu đề còn dính từ nhạy cảm hoặc trùng khuôn tiêu đề cũ mới tốn
thêm đúng một request sửa.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from reup.adapters.llm import LLMAdapter
from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.models import Transcript
from reup.policy import rules

META_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "headers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "vi": {"type": "string"}},
                "required": ["id", "vi"],
            },
        },
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "vi": {"type": "string"}},
                "required": ["id", "vi"],
            },
        },
    },
    "required": ["title", "description", "hashtags"],
}

MAX_TITLE_CHARS = 100
# Chữ cố định dịch ra không dài quá ngần này lần bản gốc (chữ Hán rất gọn) và
# không quá trần tuyệt đối — dài quá thì co chữ nhỏ tới mức không đọc nổi.
HEADER_RATIO = 2.6
HEADER_MAX_CHARS = 42
RECENT_IN_PROMPT = 8


def recent_titles(out_dir: Path, limit: int, exclude_url: str = "") -> list[str]:
    """Tiêu đề các video đã xuất gần nhất ("NN. <tiêu đề>.json"), mới nhất trước."""
    if not out_dir.is_dir():
        return []
    named = []
    # Cả thư mục con (vd. remake/) — trừ _cu/ là bản cũ đã thay.
    for p in out_dir.rglob("*.json"):
        if "_cu" in p.relative_to(out_dir).parts:
            continue
        m = re.match(r"^(\d+)\. ", p.name)
        if m:
            named.append((int(m.group(1)), p))
    titles: list[str] = []
    for _, p in sorted(named, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if exclude_url and data.get("source_url") == exclude_url:
            continue
        t = str(data.get("title") or "").strip()
        if t and t not in titles:
            titles.append(t)
        if len(titles) >= limit:
            break
    return titles


def _frame_at(job: Job, ms: int) -> Path | None:
    frames = sorted((job.root / "frames").glob("f_*.png"))
    if not frames:
        return None
    return frames[min(len(frames) - 1, max(0, round(ms / 500)))]


def _ocr_region(job: Job, region: dict, video_w: int, video_h: int) -> str:
    """Job cũ: subrect chưa lưu chữ của vùng cố định — đọc lại từ một khung."""
    try:
        from reup.media.ocr import recognize
        from reup.stages.subdetect import OCR_LANGS
    except Exception:
        return ""
    frames = sorted((job.root / "frames").glob("f_*.png"))
    if not frames:
        return ""
    frame = frames[len(frames) // 2]
    try:
        boxes = recognize(frame, video_w, video_h, OCR_LANGS)
    except Exception:
        return ""
    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    inside = [b for b in boxes if x <= b.cx <= x + w and y <= b.cy <= y + h]
    return " ".join(b.text for b in sorted(inside, key=lambda b: b.x)).strip()


def static_regions(job: Job) -> list[dict]:
    """Vùng chữ cố định (kind "watermark" của subdetect) kèm chữ gốc của nó."""
    if not job.subrect_json.exists():
        return []
    raw = json.loads(job.subrect_json.read_text(encoding="utf-8"))
    out = []
    for r in raw.get("regions", []):
        if r.get("kind") != "watermark":
            continue
        text = r.get("text") or _ocr_region(job, r, raw.get("video_w", 0), raw.get("video_h", 0))
        out.append({k: r[k] for k in ("x", "y", "w", "h")} | {"src": text})
    return out


def scene_labels(job: Job) -> list[dict]:
    """Nhãn chữ Hán trong cảnh (xem `reup.labels`). Job cũ chưa lưu thì đọc lại
    bằng OCR trên các khung subdetect đã rút (~1 phút, chỉ một lần)."""
    if not job.subrect_json.exists():
        return []
    raw = json.loads(job.subrect_json.read_text(encoding="utf-8"))
    if "labels" in raw:
        return raw["labels"]
    frames = sorted((job.root / "frames").glob("f_*.png"))
    if not frames:
        return []
    try:
        from reup.labels import find_labels, scan_frames
        from reup.media.ocr import recognize
        from reup.stages.subdetect import OCR_LANGS
    except Exception:
        return []
    w, h = raw.get("video_w", 0), raw.get("video_h", 0)
    try:
        boxes = scan_frames(frames, w, h, [recognize(f, w, h, OCR_LANGS) for f in frames],
                            OCR_LANGS)
    except Exception:
        return []
    covered = [{k: r[k] for k in ("x", "y", "w", "h")} for r in raw.get("regions", [])]
    found = find_labels(boxes, w, h, 500, covered)
    raw["labels"] = found
    atomic_write(job.subrect_json, json.dumps(raw, ensure_ascii=False, indent=2))
    return found


def build_prompt(
    transcript: Transcript,
    source_title: str,
    headers: list[dict],
    recent: list[str],
    risk_categories: list[str],
    label_texts: list[str] | None = None,
) -> str:
    body = " ".join(s.text for s in transcript.segments)[:1500]
    parts = [
        "Viết phần đăng bài cho một video ngắn tiếng Việt sắp đăng lên TikTok.\n",
        f"Tiêu đề bản gốc (chỉ để hiểu nội dung, KHÔNG chép từ nhạy cảm): "
        f"{source_title or '(không có)'}",
        f"Lời thoại tiếng Việt: {body or '(không có — video giữ tiếng gốc)'}\n",
        "Yêu cầu:",
        f"- title: tiêu đề tiếng Việt tự viết, tối đa 80 ký tự, gợi tò mò nhưng "
        "đúng sự thật, không viết hoa toàn bộ, không kèm hashtag.",
        "- description: 1-2 câu tiếng Việt mô tả đúng nội dung.",
        "- hashtags: 3-5 thẻ đúng chủ đề, không dấu #, không dấu cách, chữ thường. "
        "KHÔNG dùng thẻ chung chung (fyp, viral, xuhuong, trending) hay thẻ nền tảng "
        "khác (shorts, reels, youtube, douyin).",
    ]
    if headers:
        lines = "\n".join(
            f"  {i}. {h['src']} (tối đa {_header_limit(h['src'])} ký tự)"
            for i, h in enumerate(headers) if h.get("src")
        )
        if lines:
            parts.append(
                "- headers: video gốc có chữ cố định cháy sẵn trên hình (thường là "
                "tiêu đề). Dịch từng dòng sang tiếng Việt thật ngắn, đúng ý, để đặt "
                "đè lên đúng chỗ đó. Giữ nguyên id:\n" + lines
            )
    if label_texts:
        lines = "\n".join(f"  {i}. {t}" for i, t in enumerate(label_texts))
        parts.append(
            "- labels: chú thích chữ Hán nhỏ xuất hiện trong cảnh (vd. tên lá bài). "
            "Dịch từng cái thật ngắn (vd. 黑桃10 → 10 Bích), giữ nguyên id:\n" + lines
        )
    if recent:
        listed = "\n".join(f"  - {t}" for t in recent[:RECENT_IN_PROMPT])
        parts.append(
            "- Tiêu đề phải KHÁC hẳn khuôn các tiêu đề kênh đã đăng gần đây "
            "(đừng lặp cấu trúc hay cụm mở đầu):\n" + listed
        )
    parts.append("")
    parts.append(rules.vocab_guide(risk_categories))
    parts.append(
        '\nTrả JSON: {"title": "...", "description": "...", "hashtags": ["..."], '
        '"headers": [{"id": 0, "vi": "..."}], "labels": [{"id": 0, "vi": "..."}]}'
    )
    return "\n".join(parts)


def _header_limit(src: str) -> int:
    return max(8, min(HEADER_MAX_CHARS, round(len(src) * HEADER_RATIO)))


def build_repair_prompt(title: str, description: str, problems: list[str]) -> str:
    return (
        "Tiêu đề/mô tả video TikTok dưới đây có vấn đề, viết lại cho an toàn.\n\n"
        f"title: {title}\ndescription: {description}\n\n"
        "Vấn đề:\n" + "\n".join(f"- {p}" for p in problems) + "\n\n"
        + rules.vocab_guide()
        + '\n\nGiữ đúng ý nội dung. Trả JSON: {"title": "...", "description": "..."}'
    )


def text_problems(title: str, description: str, recent: list[str]) -> list[str]:
    """Vấn đề còn lại sau khi đã thay từ an toàn — rỗng là sạch."""
    problems = [
        f"chứa từ nhạy cảm {h.label()}"
        for h in rules.scan_text(title, "tiêu đề") + rules.scan_text(description, "mô tả")
    ]
    ratio, similar = rules.most_similar(title, recent)
    if ratio >= rules.SIMILAR_TITLE_RATIO:
        problems.append(
            f"tiêu đề trùng khuôn tiêu đề cũ “{similar}” ({ratio:.0%}) — "
            "TikTok coi đăng hàng loạt tiêu đề giống nhau là spam"
        )
    if rules.has_cjk(title) or rules.has_cjk(description):
        problems.append("còn chữ Hán — phải viết hoàn toàn bằng tiếng Việt")
    return problems


def _clean_title(text: str) -> str:
    text = rules.strip_cross_platform(rules.soften((text or "").strip()))
    return text[:MAX_TITLE_CHARS].strip()


def _clean_headers(answer: list, headers: list[dict]) -> list[dict]:
    by_id: dict[int, str] = {}
    for item in answer or []:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        try:
            by_id[int(item["id"])] = str(item.get("vi") or "")
        except (TypeError, ValueError):
            continue
    out = []
    for i, h in enumerate(headers):
        vi = rules.soften(by_id.get(i, "").strip())
        if rules.has_cjk(vi) or rules.worst(rules.scan_text(vi)) == rules.BLOCK:
            vi = ""  # thà để trống (vùng chỉ được tô nền) còn hơn in chữ sai
        out.append({**h, "vi": vi[: _header_limit(h.get("src", "")) + 10]})
    return out


def run_with(job: Job, cfg: Config, llm: LLMAdapter) -> dict:
    from reup.stages import precheck

    transcript = (
        Transcript.load(job.translation_json)
        if job.translation_json.exists()
        else Transcript(source_lang="vi", segments=[])
    )
    source_title = ""
    if job.source_info.exists():
        source_title = json.loads(
            job.source_info.read_text(encoding="utf-8")
        ).get("title", "")
    headers = static_regions(job)
    labels = scene_labels(job)
    label_texts = list(dict.fromkeys(lb["text"] for lb in labels))  # mỗi chữ dịch một lần
    recent = recent_titles(cfg.output_dir_path(), cfg.policy.recent_titles, job.source_url)
    risk = precheck.risk_categories(job)

    answer = llm.complete_json(
        build_prompt(transcript, source_title, headers, recent, risk, label_texts), META_SCHEMA
    )
    title = _clean_title(answer.get("title"))
    description = rules.strip_cross_platform(rules.soften((answer.get("description") or "").strip()))

    problems = text_problems(title, description, recent) if cfg.policy.enabled else []
    repaired = False
    if problems:
        fixed = llm.complete_json(build_repair_prompt(title, description, problems), META_SCHEMA)
        new_title = _clean_title(fixed.get("title"))
        if new_title:
            title = new_title
            description = rules.strip_cross_platform(
                rules.soften((fixed.get("description") or description).strip())
            )
            repaired = True

    # Job con của video dài đã tách: đánh số phần để người xem tìm phần tiếp.
    settings = job.settings
    if settings.get("part") and settings.get("parts"):
        suffix = f" (Phần {settings['part']}/{settings['parts']})"
        title = title[: MAX_TITLE_CHARS - len(suffix)].rstrip() + suffix

    meta = {
        "title": title,
        "description": description,
        "hashtags": rules.clean_hashtags(answer.get("hashtags")),
        "source_url": job.source_url,
        "source_title": source_title,
    }
    if headers:
        meta["headers"] = _clean_headers(answer.get("headers"), headers)
        part = settings.get("part")
        if part:
            part_tag = f" # Phần {part}"
            valid = [h for h in meta["headers"] if (h.get("vi") or "").strip()]
            if valid and not any(f"Phần {part}" in h["vi"] for h in valid):
                best = max(valid, key=lambda h: len(h["vi"]))
                best["vi"] = f"{best['vi']}{part_tag}"
    if labels:
        vi = {t["src"]: t["vi"] for t in _clean_headers(
            answer.get("labels"), [{"src": t} for t in label_texts])}
        meta["labels"] = [{**lb, "src": lb["text"], "vi": vi.get(lb["text"], "")} for lb in labels]
    if repaired:
        meta["repaired"] = problems
    atomic_write(job.meta_json, json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def run(job: Job, cfg: Config) -> None:
    from reup.adapters.registry import make_llm

    run_with(job, cfg, make_llm(cfg, "export"))


SPEC = StageSpec(name="meta", produces=("meta.json",), run=run)
