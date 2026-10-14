"""Duyệt thành phẩm theo chính sách TikTok, ngay trước khi xuất ra output/.

Rẻ trước, đắt sau:
1. Kiểm cục bộ (Apple Vision): mã QR, chữ Hán còn sót, outro kênh gốc.
2. Luật chữ trên tiêu đề / mô tả / hashtag / phụ đề / chữ đè lên hình.
3. Lỗi cứng ở 1-2 → chặn luôn, KHÔNG gọi AI.
4. Còn lại: một lượt AI vision (ảnh ghép + chữ). AI sửa được tiêu đề thì áp
   luôn (render lại nếu tiêu đề đã in lên hình); AI kết luận vi phạm thì chặn.

Bị chặn thì job dừng ở stage này với lý do cụ thể. Kết quả AI được giữ trong
`policy.rejected.json` — ép cho qua bằng `reup policy-allow <id>` rồi chạy lại
không tốn thêm token.
"""
from __future__ import annotations

import hashlib
import json

from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.policy import PolicyBlocked, local, review, rules

# Chữ Hán còn trên ngần này phần khung hình thì coi là chữ gốc sót (che hỏng).
CJK_BLOCK_RATIO = 0.25
# Từ nhạy cảm lặp ở ngần này câu thoại trở lên = chủ đề xuyên suốt video, không
# sửa bằng tiêu đề được (đã in vào phụ đề và giọng đọc). Video "Thần Bài" bị xoá
# có 6 câu như vậy.
SUBTITLE_HIT_LINES = 3
# Chỉ những nhóm hầu như không có ngữ cảnh vô hại mới chặn cứng theo cách đếm
# câu. "lừa đảo", "súng" xuất hiện suốt trong phim ngắn kể chuyện — để AI xét.
SUBTITLE_HARD_CATEGORIES = frozenset({"gambling", "gambling_theme", "drugs", "sexual", "self_harm"})


def _rejected_path(job: Job):
    return job.root / "policy.rejected.json"


def _subtitle_lines(job: Job) -> list[str]:
    if not job.translation_json.exists():
        return []
    data = json.loads(job.translation_json.read_text(encoding="utf-8"))
    return [s.get("text", "") for s in data.get("segments", [])]


def _summary(job: Job) -> str:
    path = job.root / "translate_style.json"
    if not path.exists():
        return ""
    return json.loads(path.read_text(encoding="utf-8")).get("context", "")[:400]


def _cache_key(job: Job, meta: dict, local_dict: dict, cfg: Config) -> str:
    st = job.final_mp4.stat()
    raw = json.dumps(
        [review.PROMPT_VERSION, st.st_size, st.st_mtime_ns, meta.get("title"),
         meta.get("description"), meta.get("hashtags"), local_dict,
         cfg.llm_for("policy").model],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _grouped(hits: list[rules.Hit]) -> list[str]:
    """Một dòng cho mỗi từ, gộp các chỗ xuất hiện — prompt ngắn hơn."""
    where: dict[tuple[str, str], list[str]] = {}
    for h in hits:
        places = where.setdefault((h.term, h.category), [])
        if h.where and h.where not in places:
            places.append(h.where)
    return [
        f"từ nhạy cảm “{term}” — {rules.CATEGORY_LABELS.get(cat, cat)} ({', '.join(places)})"
        for (term, cat), places in where.items()
    ]


def local_findings(rep: local.LocalReport, hits: list[rules.Hit], cfg: Config,
                   precheck: dict, similar: tuple[float, str],
                   subs: list[str] | None = None, job_id: str = "") -> tuple[list[str], list[str]]:
    """(lỗi cứng → chặn không cần AI, ghi chú → đưa vào prompt AI)."""
    hard: list[str] = []
    notes: list[str] = []
    def hard_hits(line: str) -> list[rules.Hit]:
        return [h for h in rules.scan_text(line) if h.category in SUBTITLE_HARD_CATEGORIES]

    dirty = [s for s in subs or [] if hard_hits(s)]
    if len(dirty) >= SUBTITLE_HIT_LINES:
        terms = sorted({h.term for s in dirty for h in hard_hits(s)})
        hard.append(
            f"lời thoại/phụ đề lặp từ nhạy cảm ({', '.join(terms)}) ở {len(dirty)} câu — "
            f"sửa bản dịch rồi làm lại: `reup redo {job_id} --from translate`"
        )
    if rep.qr:
        hard.append(f"có mã QR/barcode trên hình (giây {rep.qr[0][0]:.0f})")
    for h in hits:
        if h.severity == rules.BLOCK:
            hard.append(f"vi phạm chính sách: {h.label()}")
    if rep.cjk_frames:
        msg = (
            f"chữ Hán còn trên {len(rep.cjk_frames)}/{len(rep.frames)} khung "
            f"(vd. giây {rep.cjk_frames[0][0]:.0f}: “{rep.cjk_frames[0][1]}”)"
        )
        if rep.cjk_ratio >= CJK_BLOCK_RATIO:
            hard.append(msg + " — chữ gốc chưa được che/dịch")
        else:
            notes.append(msg)
    if rep.outro:
        notes.append(f"có chữ kiểu outro kênh gốc ở giây {rep.outro[0][0]:.0f}: “{rep.outro[0][1]}”")
    notes += _grouped([h for h in hits if h.severity == rules.RISKY])
    short = precheck.get("short_side") or min(rep.width, rep.height)
    if short and short < cfg.policy.min_short_side:
        notes.append(f"nguồn độ phân giải thấp (cạnh ngắn {short}px)")
    if rep.duration_s and rep.duration_s < cfg.policy.min_duration_s:
        notes.append(f"video ngắn {rep.duration_s:.1f}s")
    ratio, other = similar
    if ratio >= rules.SIMILAR_TITLE_RATIO:
        notes.append(f"tiêu đề trùng khuôn tiêu đề cũ “{other}” ({ratio:.0%}) — nên viết khác")
    return hard, notes


def _call_ai(cfg: Config, prompt: str, sheet: bytes) -> tuple[dict, dict]:
    from reup.adapters.registry import make_llm

    llm = make_llm(cfg, "policy")
    if sheet and getattr(llm, "supports_images", False):
        answer = llm.complete_json_with_images(prompt, [sheet], review.REVIEW_SCHEMA)
    else:
        answer = llm.complete_json(prompt + "\n(Không kèm được ảnh — chỉ xét phần chữ.)",
                                   review.REVIEW_SCHEMA)
    return review.normalize(answer), dict(getattr(llm, "last_usage", {}) or {})


def _rerender(job: Job, cfg: Config) -> None:
    from reup.stages import compose

    compose.run(job, cfg)


def _title_is_burned(job: Job, meta: dict) -> bool:
    """Tiêu đề có in lên hình không — đổi tiêu đề thì phải render lại."""
    return any(not (h.get("vi") or "").strip() for h in meta.get("headers") or [])


def run(job: Job, cfg: Config) -> None:
    from reup.stages import meta as meta_stage
    from reup.stages import precheck as precheck_stage

    if not cfg.policy.enabled:
        atomic_write(job.policy_json, json.dumps({"verdict": "skipped"}, ensure_ascii=False))
        return
    if not job.meta_json.exists():
        raise FileNotFoundError(f"chưa có {job.meta_json} — stage meta phải chạy trước")
    meta = json.loads(job.meta_json.read_text(encoding="utf-8"))
    pre = precheck_stage.load(job)
    subs = _subtitle_lines(job)

    rep = local.scan(job.final_mp4, job.root / "policy", cfg.policy.sample_frames)
    hits = local.text_hits(meta, " ".join(subs))
    recent = meta_stage.recent_titles(cfg.output_dir_path(), cfg.policy.recent_titles, job.source_url)
    similar = rules.most_similar(meta.get("title", ""), recent)
    hard, notes = local_findings(rep, hits, cfg, pre, similar, subs, job.id)

    result: dict = {
        "local": rep.to_dict(),
        "rule_hits": [h.to_dict() for h in hits],
        "hard": hard,
        "notes": notes,
        "ai": None,
        "changed": [],
    }
    verdict = "reject" if hard else "pass"
    risky = bool(notes or pre.get("risk_categories"))
    want_ai = cfg.policy.ai == "always" or (cfg.policy.ai == "risky" and risky)

    if not hard and want_ai:
        key = _cache_key(job, meta, rep.to_dict(), cfg)
        cached = None
        if _rejected_path(job).exists():
            old = json.loads(_rejected_path(job).read_text(encoding="utf-8"))
            if (old.get("ai") or {}).get("key") == key:
                cached = old["ai"]
        if cached:
            ai = cached
        else:
            flagged = [s for s in subs if rules.scan_text(s)]
            prompt = review.build_prompt(
                meta, _summary(job), review.excerpt_of(subs), flagged, notes,
                min(cfg.policy.sheet_frames, len(rep.frames)),
            )
            sheet = local.contact_sheet(rep.frames, cfg.policy.sheet_frames, cfg.policy.sheet_width)
            answer, usage = _call_ai(cfg, prompt, sheet)
            ai = {**answer, "key": key, "usage": usage}
        result["ai"] = ai

        blocking = [i for i in ai["issues"]
                    if i["severity"] == "high" and i["code"] not in review.TEXT_CODES]
        if ai["verdict"] == "reject" or blocking:
            verdict = "reject"
            for i in blocking or ai["issues"]:
                hard.append(f"{i['code']} {review.ISSUE_LABELS[i['code']]}"
                            + (f": {i['note']}" if i["note"] else ""))
            if not hard:
                hard.append("AI kết luận vi phạm chính sách")
        else:
            text_fix = ai["verdict"] == "fix" or any(i["code"] in review.TEXT_CODES for i in ai["issues"])
            if text_fix and review.is_safe_title(ai.get("title", "")):
                new_title = rules.strip_cross_platform(rules.soften(ai["title"]))[: meta_stage.MAX_TITLE_CHARS]
                if new_title != meta.get("title"):
                    result["changed"].append({"title": [meta.get("title"), new_title]})
                    meta["title"] = new_title
                if ai.get("description") and not rules.scan_text(ai["description"]):
                    meta["description"] = rules.strip_cross_platform(ai["description"])
                tags = rules.clean_hashtags(ai.get("hashtags"))
                if tags:
                    meta["hashtags"] = tags
                verdict = "fixed"

    if result["changed"]:
        atomic_write(job.meta_json, json.dumps(meta, ensure_ascii=False, indent=2))
        if _title_is_burned(job, meta):
            _rerender(job, cfg)
            result["rerendered"] = True

    result["verdict"] = verdict
    result["notes"] = notes
    if verdict == "reject" and not job.policy_allowed and cfg.policy.block_on_reject:
        atomic_write(_rejected_path(job), json.dumps(result, ensure_ascii=False, indent=2))
        raise PolicyBlocked(
            "Thành phẩm có nguy cơ vi phạm chính sách TikTok, KHÔNG xuất: "
            + "; ".join(hard[:6])
            + f". Sửa rồi chạy lại, hoặc ép đăng: `reup policy-allow {job.id}`."
        )
    if verdict == "reject":
        result["allowed_by_user"] = job.policy_allowed
    atomic_write(job.policy_json, json.dumps(result, ensure_ascii=False, indent=2))
    if _rejected_path(job).exists():
        _rejected_path(job).unlink()


SPEC = StageSpec(name="policy", produces=("policy.json",), run=run)
