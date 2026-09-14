"""Stage 9.5 — Chuẩn hoá phiên âm cho TTS bằng AI theo quy tắc.

Chạy sau translate/fit (hoặc sau translate), trước khi gọi TTS.
Đọc translation.json, áp dụng các quy tắc phiên âm từ tts_pronunciation_rules.md
để chuẩn hoá từ tiếng Anh, số, từ viết tắt thành chữ tiếng Việt dễ đọc cho CapCut TTS.

Ghi ra tts/pronunciation.json (map {id: tts_text}) mà KHÔNG làm thay đổi
translation.json (bản dịch gốc giữ nguyên để người dùng duyệt).
"""
from __future__ import annotations

import json
from pathlib import Path

from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.models import Transcript

BATCH_SIZE = 15

PRONOUNCE_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "tts_text": {"type": "string"},
                },
                "required": ["id", "tts_text"],
            },
        }
    },
    "required": ["segments"],
}


def load_rules(rules_path: str | Path) -> str:
    p = Path(rules_path)
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(
            f"Không tìm thấy file quy tắc phiên âm TTS tại: {p}. "
            "Kiểm tra cấu hình `tts.pronunciation_rules_path` trong config.toml"
        )
    return p.read_text(encoding="utf-8").strip()


def build_pronounce_prompt(
    segments: list,
    rules_text: str,
) -> str:
    lines = [
        "Bạn là chuyên gia chuẩn hoá văn bản tiếng Việt cho mô hình đọc giọng nói AI (TTS).",
        "Nhiệm vụ của bạn là xem xét các câu dịch tiếng Việt dưới đây và PHIÊN ÂM các từ ngoại lai (tiếng Anh),",
        "từ viết tắt, số đếm, số điện thoại, đơn vị đo lường sang cách đọc tiếng Việt tự nhiên theo ĐÚNG các quy tắc dưới đây.",
        "",
        "=== BẢNG QUY TẮC PHIÊN ÂM ===",
        rules_text,
        "=== HẾT BẢNG QUY TẮC ===",
        "",
        "=== NGUYÊN TẮC BẮT BUỘC ===",
        "1. CHỈ sửa từ/cụm từ cần phiên âm (tiếng Anh, số, viết tắt, ký hiệu).",
        "2. TUYỆT ĐỐI KHÔNG viết lại, không tóm tắt, không thêm bớt ý của câu.",
        "3. Giữ nguyên cấu trúc câu và bảo đảm số âm tiết tương đương câu gốc.",
        "4. Nếu câu đã hoàn toàn thuần Việt và không có từ nào cần phiên âm, giữ nguyên văn bản gốc.",
        "",
        "=== DANH SÁCH CÂU CẦN XỬ LÝ ===",
        json.dumps(
            [{"id": seg.id, "text": seg.text} for seg in segments],
            ensure_ascii=False,
            indent=2,
        ),
        "",
        "Trả về JSON đúng cấu trúc: {\"segments\": [{\"id\": <id>, \"tts_text\": \"<văn bản đã phiên âm>\"}]}",
    ]
    return "\n".join(lines)


def _parse_pronounce_response(response: dict | list) -> dict[int, str]:
    """Trích xuất map {id: tts_text} từ response linh hoạt của LLM."""
    by_id: dict[int, str] = {}
    if isinstance(response, dict):
        if "segments" in response and isinstance(response["segments"], list):
            for item in response["segments"]:
                if isinstance(item, dict) and "id" in item:
                    text = item.get("tts_text") or item.get("text") or ""
                    if text:
                        by_id[int(item["id"])] = str(text).strip()
        else:
            # Dạng trực tiếp {"1": "text", "2": "text"}
            for k, v in response.items():
                try:
                    seg_id = int(k)
                    if isinstance(v, str) and v.strip():
                        by_id[seg_id] = v.strip()
                    elif isinstance(v, dict):
                        text = v.get("tts_text") or v.get("text") or ""
                        if text:
                            by_id[seg_id] = str(text).strip()
                except (ValueError, TypeError):
                    continue
    elif isinstance(response, list):
        for item in response:
            if isinstance(item, dict) and "id" in item:
                text = item.get("tts_text") or item.get("text") or ""
                if text:
                    by_id[int(item["id"])] = str(text).strip()
    return by_id


def pronounce_all(
    segments: list,
    rules_text: str,
    llm,
) -> dict[int, str]:
    by_id: dict[int, str] = {}
    for i in range(0, len(segments), BATCH_SIZE):
        batch = segments[i : i + BATCH_SIZE]
        prompt = build_pronounce_prompt(batch, rules_text)
        try:
            ans = llm.complete_json(prompt, PRONOUNCE_SCHEMA)
            parsed = _parse_pronounce_response(ans)
            by_id.update(parsed)
        except Exception:
            # Best-effort: nếu một batch LLM lỗi, câu trong batch đó fallback giữ nguyên text gốc
            pass

    # Đảm bảo mọi segment đều có tts_text (nếu AI bỏ sót thì lấy text gốc)
    result = {}
    for seg in segments:
        result[seg.id] = by_id.get(seg.id) or seg.text
    return result


def run_with(job: Job, cfg: Config, llm) -> None:
    transcript = Transcript.load(job.translation_json)
    if not transcript.segments:
        raise ValueError(f"{job.translation_json} không có câu nào để phiên âm")

    rules_path = getattr(cfg.tts, "pronunciation_rules_path", "docs/tts_pronunciation_rules.md")
    rules_text = load_rules(rules_path)

    result_by_id = pronounce_all(transcript.segments, rules_text, llm)

    payload = {str(k): v for k, v in sorted(result_by_id.items())}
    job.tts_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(
        job.tts_dir / "pronunciation.json",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def run(job: Job, cfg: Config) -> None:
    from reup.adapters.registry import make_llm

    run_with(job, cfg, make_llm(cfg, "pronounce"))


SPEC = StageSpec(name="pronounce", produces=("tts/pronunciation.json",), run=run)
